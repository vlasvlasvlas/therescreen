# Therescreen Game Mode - Estado de Tareas

Este documento resume, hasta el estado actual del repo, que se hizo y que falta para dejar el modo juego totalmente habilitado y robusto.

## Objetivo

Agregar un modo separado del flujo normal de Therescreen para jugar canciones MIDI como juego de ritmo, controlando la afinacion con la inclinacion de la tapa de la Mac.

## Alcance acordado

- Mantener app normal (`therescreen.py`) sin romper comportamiento actual.
- Crear ejecutable separado para modo juego.
- Cargar MIDIs desde `midis/`.
- Puntuar por timing + afinacion, con tolerancia configurable.
- Soportar dificultad y combo.

## Implementado (completado)

### 1) Estructura base de modo juego

- [x] Nuevo backend: `therescreen_game.py`
- [x] Nuevo launcher: `therescreen-game.sh`
- [x] Nueva UI dedicada: `ui_game/index.html`, `ui_game/app.js`, `ui_game/styles.css`
- [x] Puerto por defecto separado (`8770`) para no interferir con la app principal.

### 2) Flujo MIDI

- [x] Lectura de archivos `.mid/.midi` desde `midis/`.
- [x] Parser MIDI propio (header/tracks/tempo/note on-off).
- [x] Soporte de tempo map para conversion tick -> segundos.
- [x] Seleccion automatica de pista melodica probable.
- [x] Monofonizacion para gameplay (lead jugable).
- [x] Test real con `midis/bubble.mid` parseado correctamente.

### 3) Motor de juego / scoring

- [x] Loop de juego en backend (tick continuo).
- [x] Calculo de pitch del jugador usando `lid-angle` + mapeo actual de synth.
- [x] Evaluacion por ventana temporal + error en cents.
- [x] Juicios: `perfect`, `good`, `ok`, `miss`.
- [x] Score, combo, max combo y conteo de juicios.
- [x] Score con multiplicador de combo configurable.

### 4) Configuracion de gameplay

- [x] Tolerancias configurables via API/UI.
- [x] Dificultad: `easy`, `normal`, `hard`, `custom`.
- [x] Parametros de combo configurables:
  - `comboStepPercent`
  - `comboMaxPercent`
- [x] Persistencia en `therescreen_game_settings.json`.

### 5) UI de juego

- [x] Flujo por pantallas (`seleccion`, `countdown`, `play`, `results`).
- [x] Visual principal tipo piano-roll (notas cayendo + teclado visual).
- [x] Tecla objetivo y tecla actual del jugador resaltadas.
- [x] HUD: score, combo, pitch actual, target, judgement.
- [x] Panel de settings flotante (tolerancias, lookahead, guia, combo).
- [x] Atajos numericos:
  - `1` ayuda
  - `2` panel
  - `3` start/stop

### 6) Audio de guia

- [x] Guia MIDI sintetizada en navegador (WebAudio).
- [x] Theremin de tapa sigue sonando desde backend en paralelo.
- [x] Volumen de guia configurable (`guideVolume`).
- [x] Pre-escucha corta del tema al seleccionarlo en pantalla de canciones.

### 7) Seleccion de canciones y metadata

- [x] Endpoint de listado de MIDIs con metadata por archivo (`/api/game/midis`).
- [x] Endpoint de preview+metadata por cancion (`/api/game/preview`).
- [x] Lista de canciones con stats visibles antes de jugar (duracion, notas, rango).
- [x] Cache de metadata MIDI en backend para no reprocesar innecesariamente.
- [x] Catalogo manual opcional `ui_game/midis/catalog.json` para nombre/display/game/plataforma/tags/etc.

### 8) Documentacion

- [x] README actualizado con seccion de modo juego.
- [x] Fuente recomendada de MIDIs agregada:
  - https://www.vgmusic.com/music/computer/commodore/commodore/
- [x] Nota de compatibilidad MIDI agregada (cuando conviene simplificar).

## Validaciones realizadas

- [x] `python3 -m py_compile therescreen_game.py`
- [x] `node --check ui_game/app.js`
- [x] `bash -n therescreen-game.sh`
- [x] Parse de ejemplo `bubble.mid` con rango melodico plausible.

## Pendiente (prioridad alta)

### A) Validacion end-to-end en maquina real

- [ ] Probar partida completa con tapa real y medir sensacion de latencia.
- [ ] Ajustar defaults de tolerancia por feedback de juego real.
- [ ] Validar estabilidad de audio guia + theremin simultaneo en sesiones largas.

### B) UX de resultados

- [x] Pantalla de fin de cancion (accuracy, max combo, score final).
- [x] Rank final (S/A/B/C) con criterio claro.
- [x] Boton rapido de retry + volver a canciones.

### C) Curado de MIDIs

- [ ] Agregar guia practica para preparar MIDIs complejos (si tienen demasiadas capas).
- [ ] Opcional: selector de pista manual en UI (cuando auto-melody no elige la mejor pista).

## Pendiente (prioridad media)

- [ ] Difficulty presets visibles con descripcion de impacto (timing/pitch windows).
- [x] Visual de feedback por hit (flash/color por `Perfect/Good/Ok/Miss`).
- [ ] Ajuste de scroll speed ligado a dificultad/cancion.
- [ ] Refinar scheduling de audio guia para MIDIs muy largos (mejor performance).

## Pendiente (prioridad baja)

- [ ] Persistir historico de scores por cancion.
- [ ] Leaderboard local simple (JSON).
- [ ] Export/import de perfil de settings de juego.

## Riesgos conocidos

- MIDIs con arreglos muy densos pueden requerir simplificacion para gameplay optimo.
- El auto-selector de melodia funciona bien en casos comunes, pero no garantiza la pista "correcta" en todos los archivos.
- La experiencia final depende fuertemente de calibracion de tapa/rango de frecuencia.

## Proximo corte recomendado (MVP+)

1. Validacion real de 3-5 MIDIs en hardware.
2. Ajuste de defaults de dificultad segun pruebas.
3. Pantalla final de resultados + rank.
4. Documentar "pipeline de MIDI" recomendado en README.

## Archivos principales del modo juego

- `therescreen_game.py`
- `therescreen-game.sh`
- `ui_game/index.html`
- `ui_game/app.js`
- `ui_game/styles.css`
- `midis/`
