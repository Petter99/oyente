"""Punto de entrada del ejecutable empaquetado."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from transcriptor.app import main

    main()
