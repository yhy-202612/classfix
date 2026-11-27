import sys

if __name__ == "__main__":
    from evetech.memory_cleanup import ELEVATED_CLEANUP_ARG, run_elevated_cleanup

    if sys.argv[1:] == [ELEVATED_CLEANUP_ARG]:
        raise SystemExit(run_elevated_cleanup())

    from evetech.app import main

    raise SystemExit(main())
