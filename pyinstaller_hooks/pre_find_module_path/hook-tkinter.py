def pre_find_module_path(hook_api):
    # The default PyInstaller pre-hook excludes tkinter when Tcl() cannot
    # initialize on the build machine. We bundle Tcl/Tk manually for this app.
    return
