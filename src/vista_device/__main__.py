import sys

from vista_device.server import main

if len(sys.argv) > 1 and sys.argv[1] == "selftest":
    from vista_device.selftest import main as selftest_main

    sys.exit(selftest_main(sys.argv[2:]))
main()
