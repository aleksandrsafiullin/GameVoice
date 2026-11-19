"""Entry point: `python -m gamevoice_ru` or `gamevoice-ru`.

Flags:
  --self-test                   Headless self-test: OCR+TTS+audio, prints JSON, exits 0/1.
  --self-test --strict          Strict mode: SKIP on OCR or TTS = exit 1.
  --self-test --output PATH     Also save JSON to PATH.
"""
import sys


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        from gamevoice_ru.self_test import main as self_test_main
        sys.exit(self_test_main())
    from gamevoice_ru.ui.main_window import run_gui
    run_gui()


if __name__ == "__main__":
    main()
