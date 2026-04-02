#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_FILE="$ROOT_DIR/therescreen.py"
PID_FILE="$ROOT_DIR/.therescreen.pid"
PORT_FILE="$ROOT_DIR/.therescreen.port"
DEFAULT_PORT="8765"
DEFAULT_LOG="$ROOT_DIR/therescreen.log"

usage() {
  cat <<'EOF'
Uso:
  ./therescreen.sh run [--sudo] [args de therescreen.py]
  ./therescreen.sh start [--sudo] [args de therescreen.py]
  ./therescreen.sh stop [--port N]
  ./therescreen.sh status [--port N]
  ./therescreen.sh restart [--sudo] [args de therescreen.py]

Ejemplos:
  ./therescreen.sh run --sudo
  ./therescreen.sh start --sudo --port 8765
  ./therescreen.sh stop
  ./therescreen.sh stop --port 9000
EOF
}

is_pid_alive() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

cmdline_for_pid() {
  local pid="$1"
  ps -p "$pid" -o command= 2>/dev/null || true
}

is_therescreen_pid() {
  local pid="$1"
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  [[ "$cmd" == *"therescreen.py"* ]]
}

extract_port_from_args() {
  local port="$DEFAULT_PORT"
  local prev=""
  for token in "$@"; do
    if [[ "$prev" == "--port" ]]; then
      port="$token"
      prev=""
      continue
    fi
    case "$token" in
      --port=*)
        port="${token#--port=}"
        ;;
      --port)
        prev="--port"
        ;;
      *)
        ;;
    esac
  done
  printf '%s\n' "$port"
}

find_pid_by_port() {
  local port="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

stop_pid_graceful() {
  local pid="$1"
  local wait_loops=25
  local i

  if ! is_pid_alive "$pid"; then
    return 0
  fi

  if ! kill -TERM "$pid" 2>/dev/null; then
    echo "No pude enviar SIGTERM a PID $pid (permisos). Proba: sudo ./therescreen.sh stop"
    return 1
  fi

  for ((i=0; i<wait_loops; i++)); do
    if ! is_pid_alive "$pid"; then
      return 0
    fi
    sleep 0.2
  done

  echo "PID $pid no respondio a SIGTERM, enviando SIGKILL..."
  kill -KILL "$pid" 2>/dev/null || true
  sleep 0.2
  ! is_pid_alive "$pid"
}

read_stored_port() {
  if [[ -f "$PORT_FILE" ]]; then
    cat "$PORT_FILE"
    return
  fi
  printf '%s\n' "$DEFAULT_PORT"
}

write_runtime_files() {
  local pid="$1"
  local port="$2"
  printf '%s\n' "$pid" > "$PID_FILE"
  printf '%s\n' "$port" > "$PORT_FILE"
}

cleanup_runtime_files() {
  rm -f "$PID_FILE" "$PORT_FILE"
}

run_mode() {
  local use_sudo="0"
  if [[ "${1:-}" == "--sudo" ]]; then
    use_sudo="1"
    shift
  fi

  local cmd=(python3 "$APP_FILE" "$@")
  if [[ "$use_sudo" == "1" ]]; then
    cmd=(sudo -E "${cmd[@]}")
  fi

  local port
  port="$(extract_port_from_args "$@")"
  echo "Therescreen en primer plano."
  echo "UI: http://127.0.0.1:${port}"
  echo "Para salir: Ctrl+C"
  "${cmd[@]}"
}

start_mode() {
  local use_sudo="0"
  if [[ "${1:-}" == "--sudo" ]]; then
    use_sudo="1"
    shift
  fi

  if [[ -f "$PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if is_pid_alive "$existing_pid" && is_therescreen_pid "$existing_pid"; then
      echo "Therescreen ya esta corriendo (PID $existing_pid)."
      echo "Para frenarlo: ./therescreen.sh stop"
      return 0
    fi
  fi

  local port
  port="$(extract_port_from_args "$@")"
  local cmd=(python3 "$APP_FILE" "$@")
  if [[ "$use_sudo" == "1" ]]; then
    cmd=(sudo -E "${cmd[@]}")
  fi

  nohup "${cmd[@]}" >>"$DEFAULT_LOG" 2>&1 &
  local pid=$!
  write_runtime_files "$pid" "$port"
  sleep 0.3

  if is_pid_alive "$pid"; then
    echo "Therescreen iniciado (PID $pid)."
    echo "UI: http://127.0.0.1:${port}"
    echo "Logs: $DEFAULT_LOG"
    echo "Para detener: ./therescreen.sh stop"
    return 0
  fi

  echo "No se pudo iniciar therescreen. Revisa: $DEFAULT_LOG"
  cleanup_runtime_files
  return 1
}

stop_mode() {
  local port=""
  if [[ "${1:-}" == "--port" ]]; then
    port="${2:-}"
    shift 2
  elif [[ "${1:-}" == --port=* ]]; then
    port="${1#--port=}"
    shift
  fi
  if [[ -z "$port" ]]; then
    port="$(read_stored_port)"
  fi

  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if is_pid_alive "$pid" && is_therescreen_pid "$pid"; then
      echo "Deteniendo therescreen (PID $pid)..."
      if stop_pid_graceful "$pid"; then
        cleanup_runtime_files
        echo "Therescreen detenido."
        return 0
      fi
      return 1
    fi
  fi

  local port_pid
  port_pid="$(find_pid_by_port "$port")"
  if [[ -n "$port_pid" ]] && is_therescreen_pid "$port_pid"; then
    echo "Deteniendo therescreen en puerto $port (PID $port_pid)..."
    if stop_pid_graceful "$port_pid"; then
      cleanup_runtime_files
      echo "Therescreen detenido."
      return 0
    fi
    return 1
  fi

  cleanup_runtime_files
  echo "No hay instancia de therescreen corriendo."
}

status_mode() {
  local port=""
  if [[ "${1:-}" == "--port" ]]; then
    port="${2:-}"
    shift 2
  elif [[ "${1:-}" == --port=* ]]; then
    port="${1#--port=}"
    shift
  fi
  if [[ -z "$port" ]]; then
    port="$(read_stored_port)"
  fi

  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if is_pid_alive "$pid" && is_therescreen_pid "$pid"; then
      echo "Therescreen activo (PID $pid, puerto $port)."
      return 0
    fi
  fi

  local port_pid
  port_pid="$(find_pid_by_port "$port")"
  if [[ -n "$port_pid" ]] && is_therescreen_pid "$port_pid"; then
    echo "Therescreen activo (PID $port_pid, puerto $port)."
    return 0
  fi

  echo "Therescreen no esta corriendo."
}

main() {
  if [[ ! -f "$APP_FILE" ]]; then
    echo "No se encontro $APP_FILE"
    exit 1
  fi

  local action="${1:-}"
  case "$action" in
    run)
      shift
      run_mode "$@"
      ;;
    start)
      shift
      start_mode "$@"
      ;;
    stop)
      shift
      stop_mode "$@"
      ;;
    status)
      shift
      status_mode "$@"
      ;;
    restart)
      shift
      stop_mode || true
      start_mode "$@"
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      echo "Comando desconocido: $action"
      usage
      exit 1
      ;;
  esac
}

main "$@"
