"""Entry point: `python -m gamevoice_ru` or `gamevoice-ru`."""
import sys


def main():
    from gamevoice_ru.ui.main_window import run_gui
    run_gui()


if __name__ == "__main__":
    main()
