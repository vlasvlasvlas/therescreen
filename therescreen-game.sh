#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_FILE="$ROOT_DIR/therescreen_game.py"
PID_FILE="$ROOT_DIR/.therescreen-game.pid"
PORT_FILE="$ROOT_DIR/.therescreen-game.port"
DEFAULT_PORT="8770"
DEFAULT_LOG="$ROOT_DIR/therescreen_game.log"

usage() {
  cat <<'TXT'
Uso:
  ./therescreen-game.sh run [--sudo] [args de therescreen_game.py]
  ./therescreen-game.sh start [--sudo] [args de therescreen_game.py]
  ./therescreen-game.sh stop [--port N]
  ./therescreen-game.sh status [--port N]
  ./therescreen-game.sh restart [--sudo] [args de therescreen_game.py]
TXT
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

is_game_pid() {
  local pid="$1"
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  [[ "$cmd" == *"therescreen_game.py"* ]]
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
      --port=*) port="${token#--port=}" ;;
      --port) prev="--port" ;;
      *) ;;
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
  local loops=25
  local i
  if ! is_pid_alive "$pid"; then
    return 0
  fi
  if ! kill -TERM "$pid" 2>/dev/null; then
    echo "No pude enviar SIGTERM a PID $pid (permisos). Proba: sudo ./therescreen-game.sh stop"
    return 1
  fi
  for ((i=0; i<loops; i++)); do
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
  printf '%s\n' "$1" > "$PID_FILE"
  printf '%s\n' "$2" > "$PORT_FILE"
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
  echo "Therescreen Game en primer plano"
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
    if is_pid_alive "$existing_pid" && is_game_pid "$existing_pid"; then
      echo "Therescreen Game ya esta corriendo (PID $existing_pid)."
      echo "Para frenarlo: ./therescreen-game.sh stop"
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
    echo "Therescreen Game iniciado (PID $pid)."
    echo "UI: http://127.0.0.1:${port}"
    echo "Logs: $DEFAULT_LOG"
    echo "Para detener: ./therescreen-game.sh stop"
    return 0
  fi

  echo "No se pudo iniciar Therescreen Game. Revisa: $DEFAULT_LOG"
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
    if is_pid_alive "$pid" && is_game_pid "$pid"; then
      echo "Deteniendo Therescreen Game (PID $pid)..."
      if stop_pid_graceful "$pid"; then
        cleanup_runtime_files
        echo "Therescreen Game detenido."
        return 0
      fi
      return 1
    fi
  fi

  local port_pid
  port_pid="$(find_pid_by_port "$port")"
  if [[ -n "$port_pid" ]] && is_game_pid "$port_pid"; then
    echo "Deteniendo Therescreen Game en puerto $port (PID $port_pid)..."
    if stop_pid_graceful "$port_pid"; then
      cleanup_runtime_files
      echo "Therescreen Game detenido."
      return 0
    fi
    return 1
  fi

  cleanup_runtime_files
  echo "No hay instancia de Therescreen Game corriendo."
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
    if is_pid_alive "$pid" && is_game_pid "$pid"; then
      echo "Therescreen Game activo (PID $pid, puerto $port)."
      return 0
    fi
  fi

  local port_pid
  port_pid="$(find_pid_by_port "$port")"
  if [[ -n "$port_pid" ]] && is_game_pid "$port_pid"; then
    echo "Therescreen Game activo (PID $port_pid, puerto $port)."
    return 0
  fi

  echo "Therescreen Game no esta corriendo."
}

main() {
  if [[ ! -f "$APP_FILE" ]]; then
    echo "No se encontro $APP_FILE"
    exit 1
  fi

  local action="${1:-}"
  case "$action" in
    run) shift; run_mode "$@" ;;
    start) shift; start_mode "$@" ;;
    stop) shift; stop_mode "$@" ;;
    status) shift; status_mode "$@" ;;
    restart) shift; stop_mode || true; start_mode "$@" ;;
    -h|--help|help|"") usage ;;
    *)
      echo "Comando desconocido: $action"
      usage
      exit 1
      ;;
  esac
}

main "$@"
