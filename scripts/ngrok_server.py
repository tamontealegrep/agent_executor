"""
Module.

    Levanta el servidor local Y el túnel ngrok con un solo comando.

    Uso:
        python scripts/ngrok_server.py

    Lee HOST, PORT, NGROK_TOKEN y NGROK_DOMAIN desde el .env del root. Arranca
    main.py como subproceso, espera a que el puerto responda, y recién ahí abre
    el túnel. Al cerrar (CTRL+C, o si ngrok termina), también detiene el servidor
    que levantó.
"""

from pathlib import Path
import os
import socket
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from discovery import BASE_DIR, discover_agents, get_hub_settings


def _wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.3)
    return False


def _kill_local_ngrok_processes() -> bool:
    """
Try to terminate local ngrok processes (best-effort).

    Returns True if a kill command executed without raising, False otherwise.

    Returns:
        The result.
"""
    try:
        if os.name == "nt":
            # Windows
            result = subprocess.run(["taskkill", "/F", "/IM", "ngrok.exe"], text=True, capture_output=True)
            return result.returncode == 0
        else:
            # Unix-like (may not exist on all systems, best-effort)
            result = subprocess.run(["pkill", "-f", "^ngrok( .*)?$"], text=True, capture_output=True)
            return result.returncode == 0
    except Exception:
        return False


def _open_ngrok_tunnel(domain: str, port: int) -> None:
    # Always prefer pooling to avoid race where first client is non-pooled.
    pooled_command = ["ngrok", "http", f"--domain={domain}", "--pooling-enabled", str(port)]
    result = subprocess.run(pooled_command, text=True, capture_output=True)
    if result.returncode == 0:
        return

    # If the endpoint is already online (ERR_NGROK_334), we can optionally
    # try to kill local ngrok processes and retry once (opt-in via env var).
    stderr_text = result.stderr or ""
    if "ERR_NGROK_334" in stderr_text:
        print("El dominio ya está online. Verificando opciones...")
        kill_flag = os.getenv("NGROK_KILL_EXISTING", "").strip().lower() in {"1", "true", "yes"}
        if kill_flag:
            print("Intentando detener procesos locales de ngrok y reintentando...")
            if _kill_local_ngrok_processes():
                time.sleep(1.0)
                retry = subprocess.run(pooled_command, text=True, capture_output=True)
                if retry.returncode == 0:
                    return
                stderr_text = retry.stderr or stderr_text
            else:
                print("No se pudo detener ngrok localmente (o no estaba ejecutándose).")

        # If we get here, we still failed. Provide a clear guidance and raise.
        stderr_text += (
            "\nSugerencia: Detén el endpoint activo o arranca TODOS los clientes "
            "con '--pooling-enabled'. También puedes exportar NGROK_KILL_EXISTING=1 "
            "para que este script intente cerrar ngrok local antes de reintentar."
        )
        raise subprocess.CalledProcessError(
            result.returncode,
            pooled_command,
            output=result.stdout,
            stderr=stderr_text,
        )

    # Other errors: surface as-is for visibility
    raise subprocess.CalledProcessError(
        result.returncode,
        pooled_command,
        output=result.stdout,
        stderr=result.stderr,
    )


if __name__ == "__main__":
    settings = get_hub_settings()
    probe_host = "127.0.0.1" if settings.host == "0.0.0.0" else settings.host

    if not settings.ngrok_domain:
        print("ERROR: falta NGROK_DOMAIN en el .env del root", file=sys.stderr)
        sys.exit(1)

    agentes = ", ".join(discover_agents()) or "(ninguno encontrado)"
    print(f"Agentes: {agentes}")
    print(f"Levantando el servidor en http://{settings.host}:{settings.port} ...")

    server_process = subprocess.Popen([sys.executable, "main.py"], cwd=PROJECT_ROOT)

    try:
        if not _wait_for_port(probe_host, settings.port):
            print("ERROR: el servidor no respondió a tiempo.", file=sys.stderr)
            sys.exit(1)

        print("Servidor listo.\n")

        if settings.ngrok_token:
            subprocess.run(["ngrok", "config", "add-authtoken", settings.ngrok_token], check=True)

        print(f"Abriendo túnel: https://{settings.ngrok_domain} -> http://127.0.0.1:{settings.port}")
        _open_ngrok_tunnel(settings.ngrok_domain, settings.port)

    except FileNotFoundError:
        print("ERROR: no se encontró el ejecutable 'ngrok' en el PATH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        print(f"\nERROR al ejecutar ngrok: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nDeteniendo...")
    finally:
        print("Deteniendo el servidor...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
