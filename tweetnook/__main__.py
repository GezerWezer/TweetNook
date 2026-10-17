from tweetnook.worker_logging import configure_worker_output


def main() -> None:
    configure_worker_output()
    # Install the stream before CLI imports so startup diagnostics are captured too.
    from tweetnook.cli import app

    app()


if __name__ == "__main__":
    main()
