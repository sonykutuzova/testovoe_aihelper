import argparse
from .api import make_server
from .provider import OpenAIProvider


def main():
    parser = argparse.ArgumentParser(description="API для подготовки карточек товаров")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535.")
    try:
        with make_server(OpenAIProvider(), args.port) as server:
            print(f"API: http://127.0.0.1:{server.server_port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        parser.exit(1, f"Не удалось запустить сервер: {exc}\n")


if __name__ == "__main__":
    main()
