import labscript_utils.h5_lock
from labscript_utils.ls_zprocess import ProcessTree
import threading, os, traceback
import time

process_tree = ProcessTree.instance()
process_tree.zlock_client.set_process_name("probe-error-scope")

from ScopeInterface import ScopeServer

# port = 2626
# kserver = ScopeServer("DS1ZA205020656", port, "ProbeErrorScope")
# kserver.shutdown_on_interrupt()


def start_server():
    port = 2626
    kserver = ScopeServer("DS1ZA205020656", port, "ProbeErrorScope")
    kserver.shutdown_on_interrupt()


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
