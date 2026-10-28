"""Frozen launcher: app-owned environment and worker dispatch precede heavy imports."""

if __name__ == '__main__':
    import sys
    import traceback
    from flir_seq_tool.runtime_paths import configure_portable_environment, application_root

    try:
        configure_portable_environment()
        from multiprocessing import freeze_support
        freeze_support()

        # A windowed executable has no standard streams. Keep diagnostics local.
        log = None
        if sys.stdout is None or sys.stderr is None:
            log = (application_root() / 'logs' / 'application.log').open('a', encoding='utf-8', buffering=1)
            sys.stdout = sys.stderr = log

        if '--diagnose' in sys.argv:
            from flir_seq_tool.portable_diagnostics import main
            raise SystemExit(main())

        from flir_seq_tool.desktop import launch_desktop
        code = launch_desktop()
        if code:
            from flir_seq_tool.desktop import show_startup_error
            show_startup_error('Could not open the selected file. See logs/application.log for details.')
        raise SystemExit(code)
    except SystemExit:
        raise
    except Exception:
        error = traceback.format_exc()
        try:
            target = application_root() / 'logs' / 'startup-error.log'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(error, encoding='utf-8')
            message = f'{error}\nDetails: {target}'
        except OSError:
            message = error + '\nThe program folder must be writable.'
        from flir_seq_tool.desktop import show_startup_error
        show_startup_error(message)
        raise SystemExit(1)
