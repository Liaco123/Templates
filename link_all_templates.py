from setup_templates import main as setup_templates_main


def main():
    print("[WARN] link_all_templates.py 已兼容保留；新入口是 setup_templates.py 或 setup_all.py。")
    setup_templates_main(["--link"])

if __name__ == "__main__":
    main()
