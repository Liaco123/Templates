import setup


def main(argv=None):
    print("[警告] setup_all.py 已兼容保留；新入口是 setup.py。")
    translated = []
    for arg in argv or []:
        translated.append("--skip-env" if arg == "--skip-dev" else arg)
    return setup.main(translated)


if __name__ == "__main__":
    raise SystemExit(main())
