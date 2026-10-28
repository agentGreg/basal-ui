"""basal-ui's command: the engine check runs before anything imports the engine (basal_ui.app does)."""


def main():
    from .engine import check
    check()
    from .app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
