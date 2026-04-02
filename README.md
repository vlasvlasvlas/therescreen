# therescreen

Therescreen es un instrumento tipo theremin para Mac que usa la apertura/cierre de la tapa como control continuo de pitch.

Base tecnica: https://github.com/pirate/mac-hardware-toys

## Que hace

- Pitch continuo con `lid-angle`.
- Sonido estilo theremin (mono, glide, vibrato, filtro, delay, reverb).
- Fondo negro + color interpolado entre 2 colores segun tapa.
- Control opcional de brillo de teclado y pantalla de hardware.
- Panel web en vivo con presets de sintetizador en YAML.
- Visual opcional de frecuencia+nota y piano roll.
- Gizmo reactivo opcional (ojos/cara reaccionan al movimiento y al tono).

## Requisitos

- macOS
- Python 3.9+
- Dependencias:

```bash
pip install mac-hardware-toys
pip install numpy
```

Notas:

- Para acceso a sensores y control de brillo normalmente vas a necesitar `sudo`.
- Si no tenes `numpy`, el modo audio no arranca.

## Estructura del proyecto

- `therescreen.py`: backend principal (sensores, audio engine, brillo, API, servidor web).
- `ui/index.html`: interfaz (pantalla, modal de ayuda, sidebar de configuracion).
- `ui/app.js`: render realtime y logica de controles.
- `ui/styles.css`: estilos.
- `synth_presets.yaml`: presets de sintetizador.
- `therescreen.sh`: wrapper CLI amigable (`run/start/stop/status/restart`).
- `theremin_lid.py`: prototipo anterior.

## Ejecutar

### Opcion recomendada (script amigable)

```bash
./therescreen.sh run --sudo
```

Tambien podes usar:

```bash
./therescreen.sh start --sudo --port 8765
./therescreen.sh status
./therescreen.sh stop
./therescreen.sh restart --sudo
```

### Opcion directa

```bash
sudo python3 therescreen.py
```

Abrir en navegador:

```text
http://127.0.0.1:8765
```

## Como se usa en vivo

- Atajo `1`: abrir/cerrar ayuda (`!?`).
- Atajo `2`: abrir/cerrar panel (`icono engranaje`).
- Atajo `3`: mute/unmute audio.

Flujo tipico:

1. Abri la app en fullscreen.
2. Move la tapa para controlar pitch.
3. Ajusta color, audio y hardware desde el sidebar.
4. Elegi un preset YAML o guarda uno nuevo.

## Sidebar de control

### Tab Pantalla

- `Color A (tapa abierta)` y `Color B (tapa cerrada)`.
- Inversion de color e inversion de intensidad.
- Mostrar/ocultar `frecuencia + nota`.
- Mostrar/ocultar `piano roll`.
- Mostrar/ocultar `Gizmo interactivo`.
- Calibracion de angulo (`angleMin`, `angleMax`).

### Tab Teclado

- Habilitar/deshabilitar control de teclado.
- Invertir mapeo teclado.
- Definir brillo minimo y maximo.

### Tab Sonido

- Volumen 0-100 + boton `Mute`.
- `Volumen por eje Y del mousepad` (arriba = mas volumen, abajo = menos).
- Presets YAML: aplicar, recargar, guardar nuevo.
- Parametros rapidos y avanzados del sintetizador.
- `Anclar frecuencia en 90`: permite correr el espectro para definir que Hz queres en ese angulo.

## Presets YAML

Archivo por defecto: `synth_presets.yaml`

Cada preset define **todos** los parametros de `synth`.

Ejemplo:

```yaml
classic_theremin:
  waveform: sine
  minHz: 130.81
  maxHz: 1760.0
  anchorAt90Enabled: false
  freqAt90Hz: 880.0
  lowVolume: 0.03
  highVolume: 0.66
  attackMs: 8.0
  releaseMs: 120.0
  glideMs: 24.0
  vibratoRateHz: 6.1
  vibratoDepthCents: 8.0
  vibratoAmbientCents: 16.0
  cutoffHz: 4200.0
  cutoffFollow: 0.3
  delayMs: 360.0
  delayFeedback: 0.22
  delayMix: 0.14
  reverbMix: 0.3
  masterGain: 0.9
```

Uso desde UI:

1. Elegi preset en el dropdown.
2. Se aplica automaticamente al cambiar (o con `Aplicar preset`).
3. Ajusta parametros.
4. Escribi nombre y guarda con `Guardar preset`.
5. Si editaste YAML a mano, usa `Recargar YAML`.

## Configuracion persistente

Archivo por defecto: `therescreen_config.json`

- Guarda estado de UI, audio, brillo, sensor y preset activo.
- Se actualiza al cambiar controles persistentes.
- Cambios de runtime de alta frecuencia (como volumen por pointer Y) usan endpoint runtime para evitar escritura excesiva.

## Argumentos CLI utiles

```bash
# red
--host 127.0.0.1
--port 8765

# audio
--sample-rate 48000
--block-size 96
--no-audio

# brillo / sensores
--no-brightness
--no-ambient
--lid-command "lid-angle"
--ambient-command "ambient-light"
--keyboard-set-command "keyboard-brightness --set={value}"
--screen-set-command "screen-brightness --set={value}"

# archivos
--config-file therescreen_config.json
--presets-file synth_presets.yaml
--ui-dir ui

# logs
--log-file therescreen.log
--log-level INFO
```

Ejemplos:

```bash
sudo python3 therescreen.py --port 9000
sudo python3 therescreen.py --no-brightness
sudo python3 therescreen.py --no-audio
sudo python3 therescreen.py --presets-file /ruta/mis_presets.yaml
sudo python3 therescreen.py --log-level DEBUG
```

## Valores por defecto importantes

- Color tapa abierta: rosa (`#ff4fd8`).
- Color tapa cerrada: amarillo (`#ffd400`).
- Teclado invertido por defecto:
  - tapa abierta -> menos backlight
  - tapa cerrada -> mas backlight
- Preset inicial: `classic_theremin`.

## Audio engine (resumen tecnico)

- Smoothing de pitch/amp/ambient para evitar saltos.
- Glide continuo para comportamiento tipo theremin.
- Vibrato con depth dependiente de ambient light.
- Filtro low-pass + delay + reverb.
- High-pass + soft clip para reducir ruido DC y picos.
- Mute con rampa de ganancia para reducir clicks.

## Piano roll y pitch overlay

- El texto de pitch muestra: `Hz + nota`.
- El piano roll marca aguja + tecla activa.
- El rango visual del piano roll se ajusta al rango efectivo alcanzable, incluyendo ancla de 90.

## Logs

Se escriben en:

- `stderr` (terminal)
- archivo rotativo (`therescreen.log` por defecto, 2MB x 5)

Ver en vivo:

```bash
tail -f therescreen.log
```

## Parar la app de forma limpia

Si la corriste en primer plano:

- `Ctrl+C`

Si la corriste en background con script:

```bash
./therescreen.sh stop
```

Si usaste otro puerto:

```bash
./therescreen.sh stop --port 9000
```

## Troubleshooting

### "Address already in use"

```bash
sudo python3 therescreen.py --port 8766
```

Tambien podes detener instancia previa:

```bash
./therescreen.sh stop --port 8765
```

### No hay audio

- Verifica que `numpy` este instalado.
- Verifica que `speaker` exista en PATH.
- Revisar logs (`--log-level DEBUG`).

### No responde lid/ambient

- Ejecuta con `sudo`.
- Verifica `lid-angle --json` y `ambient-light --json` por separado.

### Brillo de hardware no cambia

- Verifica comandos `keyboard-brightness` y `screen-brightness`.
- Revisa permisos (normalmente requiere `sudo`).

## Repos

- Repo de este proyecto: https://github.com/vlasvlasvlas/therescreen
- Base tecnica de sensores: https://github.com/pirate/mac-hardware-toys
