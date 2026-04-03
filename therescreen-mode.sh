#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_SCRIPT="$ROOT_DIR/therescreen.sh"
GAME_SCRIPT="$ROOT_DIR/therescreen-game.sh"

usage() {
  cat <<'TXT'
Launcher unificado de Therescreen

Uso simple:
  ./therescreen-mode.sh menu

Comandos recomendados (sin terminos tecnicos):
  ./therescreen-mode.sh jugar [--sudo] [args...]
  ./therescreen-mode.sh theremin [--sudo] [args...]
  ./therescreen-mode.sh dejar-juego [--sudo] [args...]
  ./therescreen-mode.sh dejar-theremin [--sudo] [args...]
  ./therescreen-mode.sh estado
  ./therescreen-mode.sh cerrar

Compatibles (tecnicos):
  run/start/stop/status/restart

Ejemplos:
  ./therescreen-mode.sh jugar --sudo
  ./therescreen-mode.sh theremin --sudo
  ./therescreen-mode.sh dejar-juego --sudo --port 8770
  ./therescreen-mode.sh estado
  ./therescreen-mode.sh cerrar
TXT
}

to_lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

resolve_script() {
  local mode="$1"
  case "$(to_lower "$mode")" in
    normal|core|therescreen)
      printf '%s\n' "$CORE_SCRIPT"
      ;;
    game|juego)
      printf '%s\n' "$GAME_SCRIPT"
      ;;
    *)
      return 1
      ;;
  esac
}

ensure_scripts() {
  if [[ ! -f "$CORE_SCRIPT" ]]; then
    echo "No se encontro $CORE_SCRIPT"
    exit 1
  fi
  if [[ ! -f "$GAME_SCRIPT" ]]; then
    echo "No se encontro $GAME_SCRIPT"
    exit 1
  fi
}

do_action() {
  local action="$1"
  local mode="$2"
  shift 2

  case "$(to_lower "$action")" in
    ahora) action="run" ;;
    dejar) action="start" ;;
    cerrar) action="stop" ;;
    estado) action="status" ;;
    jugar) action="run"; mode="game" ;;
    theremin) action="run"; mode="normal" ;;
    dejar-juego) action="start"; mode="game" ;;
    dejar-theremin) action="start"; mode="normal" ;;
  esac

  if [[ "$(to_lower "$mode")" == "all" ]]; then
    case "$action" in
      stop|status)
        "$CORE_SCRIPT" "$action" "$@" || true
        "$GAME_SCRIPT" "$action" "$@" || true
        return 0
        ;;
      *)
        echo "El modo 'all' solo aplica para stop/status."
        return 1
        ;;
    esac
  fi

  local target
  if ! target="$(resolve_script "$mode")"; then
    echo "Modo invalido: $mode"
    usage
    return 1
  fi

  "$target" "$action" "$@"
}

menu() {
  echo ""
  echo "Therescreen launcher"
  echo "1) Jugar ahora"
  echo "2) Theremin ahora"
  echo "3) Dejar juego abierto"
  echo "4) Dejar theremin abierto"
  echo "5) Ver estado de todo"
  echo "6) Cerrar todo"
  echo "7) Salir"
  echo ""

  read -r -p "Elegi opcion [1-7]: " opt
  case "${opt:-}" in
    1)
      read -r -p "Usar sudo? [Y/n]: " ans
      local ans_l
      ans_l="$(to_lower "${ans:-}")"
      if [[ -z "${ans:-}" || "$ans_l" == "y" || "$ans_l" == "yes" || "$ans_l" == "s" || "$ans_l" == "si" ]]; then
        do_action jugar game --sudo
      else
        do_action jugar game
      fi
      ;;
    2)
      read -r -p "Usar sudo? [Y/n]: " ans
      local ans_l
      ans_l="$(to_lower "${ans:-}")"
      if [[ -z "${ans:-}" || "$ans_l" == "y" || "$ans_l" == "yes" || "$ans_l" == "s" || "$ans_l" == "si" ]]; then
        do_action theremin normal --sudo
      else
        do_action theremin normal
      fi
      ;;
    3)
      read -r -p "Usar sudo? [Y/n]: " ans
      local ans_l
      ans_l="$(to_lower "${ans:-}")"
      if [[ -z "${ans:-}" || "$ans_l" == "y" || "$ans_l" == "yes" || "$ans_l" == "s" || "$ans_l" == "si" ]]; then
        do_action dejar-juego game --sudo
      else
        do_action dejar-juego game
      fi
      ;;
    4)
      read -r -p "Usar sudo? [Y/n]: " ans
      local ans_l
      ans_l="$(to_lower "${ans:-}")"
      if [[ -z "${ans:-}" || "$ans_l" == "y" || "$ans_l" == "yes" || "$ans_l" == "s" || "$ans_l" == "si" ]]; then
        do_action dejar-theremin normal --sudo
      else
        do_action dejar-theremin normal
      fi
      ;;
    5)
      do_action status all
      ;;
    6)
      do_action stop all
      ;;
    7)
      exit 0
      ;;
    *)
      echo "Opcion invalida."
      return 1
      ;;
  esac
}

main() {
  ensure_scripts

  local action="${1:-menu}"
  case "$(to_lower "$action")" in
    -h|--help|help)
      usage
      exit 0
      ;;
    menu|choose)
      menu
      exit 0
      ;;
    run|start|stop|status|restart)
      local mode="${2:-}"
      if [[ -z "$mode" ]]; then
        echo "Falta modo (normal|game|all)."
        usage
        exit 1
      fi
      shift 2
      do_action "$action" "$mode" "$@"
      ;;
    ahora|dejar|cerrar|estado)
      local mode="${2:-all}"
      if [[ "$#" -ge 2 ]]; then
        shift 2
      else
        shift 1
      fi
      do_action "$action" "$mode" "$@"
      ;;
    jugar|theremin|dejar-juego|dejar-theremin)
      shift 1
      do_action "$action" all "$@"
      ;;
    *)
      echo "Comando invalido: $action"
      usage
      exit 1
      ;;
  esac
}

main "$@"
