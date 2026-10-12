import os, sys

sys.path.append("X:\\")
import labscript_utils.h5_lock
from labscript_utils.ls_zprocess import ProcessTree
import zprocess

import pyvisa
import numpy as np
import time
from device_server import DeviceServer
import h5py
import threading, os, traceback
import CavityPaths

process_tree = ProcessTree.instance()
process_tree.zlock_client.set_process_name("rigol-scope")


class ScopeInterface:
    def __init__(self, identifying_string):
        self.mdepth = 30000
        self.resource_manager = pyvisa.ResourceManager()
        all_devices = self.resource_manager.list_resources()
        acceptable_devices = [i for i in all_devices if identifying_string in i]
        if acceptable_devices == []:
            raise Exception(
                f"Could not find device with identifying string {identifying_string}"
                + "Found devices:\n"
                + "\n".join(all_devices)
            )
        print(all_devices)
        print(identifying_string)
        self.scope = self.resource_manager.open_resource(acceptable_devices[0])
        self.scope.timeout = 500 # milliseconds
        # self.scope.read_termination="\n"
        sid = self.scope.query("*IDN?")
        self.n_channels = self.scope.query_ascii_values(":SYST:RAM?")[0]
        print(f"Connected to scope {sid} with {self.n_channels} channels")
        self.scope.write(f":ACQ:MDEP {self.mdepth}")
        self.scope.write(":WAV:POIN 15000")
        mdep = self.scope.query_ascii_values(":ACQ:MDEP?")
        acq_points = self.scope.query_ascii_values(":WAV:POIN?")
        print(f"Set memdepth to {mdep} (should be 30,000), Max possible acquisition pts: {acq_points}")

    def get_voltage(self, channel, stop=True):
        """
        Inputs:
            Channel - integer

        Outputs:
            time_relative_trigger - numpy array of time (in seconds) relative to
            the trigger pulse
            data - numpy array of voltage at each time
        """
        try:
            print(self.scope.query(":SYSTEM:ERROR?"))

            if channel > self.n_channels or channel < 1:
                raise Exception(f"Channel must be between 1 and {self.n_channels}.")
            if stop:
                self.scope.write(":STOP")
            self.scope.write(f":WAV:SOUR CHAN{channel}")
            self.scope.write(":WAV:MODE raw")
            self.scope.write(":WAV:FORM BYTE")
            # data = self._read_full_waveform_raw()
            self.scope.write(f":WAV:STOP {self.mdepth}")
            self.scope.write(":WAV:DATA?")
            data = self.scope.read_raw()
            #data = self.scope.query(':WAV:DATA?')
            data = np.frombuffer(data, dtype="uint8", offset=12)
            (
                _,
                _,
                _,
                _,
                xinc,
                xorigin,
                xref,
                yinc,
                yorigin,
                yref,
            ) = self.scope.query_ascii_values(":WAV:PRE?")
            print(xinc)
            data = (data - yref - yorigin) * yinc
            data = data[:-1]
            # time_relative_trigger = np.arange(0, -wvf_points * xinc, -xinc) - xorigin
            wvf_points = len(data)
            time_relative_trigger = np.arange(0, wvf_points * xinc, xinc) + xorigin
            if channel == 1:
                print("Number of points in Channel 1", wvf_points)
            return time_relative_trigger, data
        except:
            # print(self.scope.query(":SYSTEM:ERROR?"))
            traceback.print_exc()
            return np.zeros(30000), np.zeros(30000)
        

    def run(self):
        self.scope.write(":RUN")

    def get_all_voltages(self, channels, stop=True):
        """
        Get the voltage trace on the screen of every channel

        Outputs:
            time - numpy array of times that data was taken
            datas - list of voltages, corresponding to channel 1..n
        """
        channels = np.array(channels)
        values = [self.get_voltage(i, stop) for i in channels + 1]
        times, datas = zip(*values)
        return times[0], datas

    def set_timestep(self, timestep):
        """
        Set the scope timebase to timestep secs/division

        Input:
            timestep: float, secs/division
        """
        current_timestep = float(self.scope.query_ascii_values(":TIM:SCAL?")[0])
        time.sleep(0.1)
        if timestep != current_timestep:
            print(f"Setting timestep to {timestep}")
            print(f"Previous value was {current_timestep}")
            self.scope.write(f":TIM:SCAL {timestep:f}")
            time.sleep(0.1)

    def set_timeoffset(self, offset):
        """
        Set the scope horizontal offset relative to the trigger
        Input:
            offset: float, secs
        """
        current_offset = float(self.scope.query_ascii_values(":TIM:OFFS?")[0])
        time.sleep(0.1)
        if offset != current_offset:
            print(f"Setting offset to {offset}")
            print(f"Previous value was {current_offset}")
            self.scope.write(f":TIM:OFFS {offset:f}")
            time.sleep(0.05)


class ScopeServer(DeviceServer):
    def __init__(self, scope_name, port, device_name):
        print(port)
        super().__init__(port)
        print("Starting Scope Server")
        self.name = scope_name
        self.interface = ScopeInterface(scope_name)
        self.channels = 1
        self.device_name = device_name
        self.save_path = CavityPaths.rigol_trace_location  # "C:/rigol_traces/"
        self.timestep = 0
        self.offset = 0

    def transition_to_buffered(self, h5_filepath):
        """
        Set timebase to correct values
        """
        print("Transitioning to buffer for ScopeInterface")
        with h5py.File(h5_filepath, "r") as f:
            dset = f"/devices/{self.device_name}"
            parameters = f[dset].attrs
            timestep = parameters["timestep"]
            offset = parameters["offset"]
            self.channels = parameters["channels"]
            self.names = parameters["names"]
        dochange = False
        if offset != self.offset:
            self.offset = offset
            dochange = True
        if timestep != self.timestep:
            self.timestep = timestep
            dochange = True
        if dochange:
            try:
                self.interface.set_timestep(timestep)
                self.interface.set_timeoffset(offset)
            except:
                traceback.print_exc()
                print(
                    "Mistakes happen. What's going wrong here is that for some reason the ScopeInterface is unable to set the right timestep or time offset"
                )
        self.interface.run()
        return True

    def write_voltages(self, h5_filepath):
        times, voltages = self.interface.get_all_voltages(np.array(self.channels))
        head, tail = os.path.split(
            labscript_utils.shared_drive.path_to_local(h5_filepath)
        )

        print('Head', head, 'Tail', tail)

        shot_prefix = tail.split(".")[0]

        print("Shot prefix", shot_prefix)

        for name, voltage in zip(self.names, voltages):
            file_location = f"{self.save_path}RIGOL{name}_{shot_prefix}.npy"
            time_location = f"{self.save_path}RIGOLtimes{name}_{shot_prefix}.npy"
            print(f"Saving {file_location}")
            np.save(file_location, voltage)
            np.save(time_location, times)
        print("done saving")

    def transition_to_static(self, h5_filepath):
        """
        Read scope values, write to h5 file.
        """
        print("Transitioning to static for ScopeInterface")
        save_thread = threading.Thread(target=self.write_voltages, args=(h5_filepath,))
        save_thread.start()
        print('Channels', self.channels, 'Channel names', self.names)
        # times, voltages = self.interface.get_all_voltages(np.array(self.channels))
        # print(voltages)
        # # while True:
        # #     try:
        # with h5py.File(h5_filepath, 'a') as f:
        #     group = f['data']
        #     try:
        #         trace_group = f.create_group('ScopeTraces')
        #     except ValueError:
        #         trace_group = f['ScopeTraces']
        #         print("Group Already existed")
        #     for name, voltage in zip(self.names, voltages):
        #         trace_group.create_dataset('times' + name, data=times, dtype='float')
        #         trace_group.create_dataset(name, data=voltage, dtype='float')

    def abort(self):
        """To be overridden by subclasses. Return cameras and any other state
        to one in which transition_to_buffered() can be called again. abort()
        will be called if there was an exception in either
        transition_to_buffered() or transtition_to_static(), and so should
        ideally be written to return things to a sensible state even if those
        methods did not complete. Like any cleanup function, abort() should
        proceed to further cleanups even if earlier cleanups fail. As such it
        should make liberal use of try: except: blocks, so that an exception
        in performing one cleanup operation does not stop it from proceeding
        to subsequent cleanup operations"""
        print("abort")



# change made by Merrick 27 Feb 2-26
# if __name__ == "__main__":
#     port = 2625

#     kserver = ScopeServer(
#         "USB0::0x1AB1::0x04CE::DS1ZA205020654::INSTR", port, "RigolScope"
#     )
#     kserver.shutdown_on_interrupt()

def start_server():
    port = 2625

    kserver = ScopeServer(
        "USB0::0x1AB1::0x04CE::DS1ZA205020654::INSTR", port, "RigolScope"
    )
    kserver.shutdown_on_interrupt()

# if __name__ == "__main__":
#     while True:
#         try:
#             start_server()
#             # If shutdown_on_interrupt() returns normally, exit the loop:
#             break
#         except Exception:
#             print("[rigol-scope] crashed; restarting whole process...")
#             traceback.print_exc()
#             time.sleep(2)
#             os.execv(sys.executable, [sys.executable] + sys.argv)

RESTART_DELAY = 5

def main():
    while True:
        try:
            start_server()
        except Exception:
            traceback.print_exc()
        
        time.sleep(RESTART_DELAY)

if __name__ == "__main__":
    main()


