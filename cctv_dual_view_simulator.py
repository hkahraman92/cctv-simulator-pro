from cctv_simulator.theme import create_app_window
from cctv_simulator.errors import install_error_reporting
from cctv_simulator.ui.main_window import DualViewCCTVDesignApp

if __name__ == "__main__":
    root = create_app_window()
    # Windowed builds have no stderr: without this, any exception raised inside
    # a Tk callback vanishes and the UI just looks stuck.
    install_error_reporting(root)
    app = DualViewCCTVDesignApp(root)
    root.mainloop()
