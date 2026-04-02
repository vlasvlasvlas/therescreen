# therescreen

Instrumento tipo theremin para Mac usando `mac-hardware-toys`:

- Pitch continuo al abrir/cerrar la tapa (`lid-angle`)
- Sonido tipo theremin clasico (mono, glide, ADSR suave, vibrato, delay, reverb)
- Pantalla web negra con color interpolado entre 2 colores (A/B)
- Control de brillo de teclado y pantalla segun angulo de tapa
- UI minimal con 2 botones arriba a la derecha:
- `!?` abre modal de ayuda
- `⚙` abre sidebar de configuracion en vivo
- Sidebar simplificado en 3 tabs:
- `Pantalla`
- `Teclado`
- `Sonido`

## Sensores y tecnologia base

Basado en: https://github.com/pirate/mac-hardware-toys

Sensores disponibles alli:

- `accelerometer`
- `microphone`
- `lid-angle`
- `ambient-light`
- `gyroscope`

En esta app se usan principalmente `lid-angle` (control principal) y `ambient-light` (modulacion extra del vibrato si esta activo).

## Archivos principales

- `therescreen.py`: backend completo (sensores + sintesis + brillo + API + servidor web)
- `ui/index.html`: pantalla negra, modal y sidebar
- `ui/styles.css`: estilos
- `ui/app.js`: render realtime + controles de configuracion
- `theremin_lid.py`: prototipo CLI anterior

## Instalacion

```bash
pip install mac-hardware-toys
```

Si no tenes `numpy`:

```bash
pip install numpy
```

## Ejecutar

Recomendado (por permisos de sensores y control HW):

```bash
sudo python3 therescreen.py
```

Luego abrir:

```text
http://127.0.0.1:8765
```

## Uso rapido

1. Abri la URL y deja esa ventana en fullscreen si queres modo performance.
2. Move la tapa: cambia el tono, color de pantalla y brillo.
3. Boton `!?`: info/modal (atajo `1`).
4. Boton `⚙` (atajo `2`):
   Tab `Pantalla`: color A/B, inversion, encendido/apagado y calibracion de angulo.
   Tab `Pantalla`: opcion para mostrar/ocultar en pantalla `frecuencia + nota`.
   Tab `Teclado`: encendido/apagado, inversion y rango de brillo teclado.
   Tab `Sonido`: parametros de sintesis + FX + presets YAML (dropdown).

## Opciones utiles

Sin audio (solo visual + brillo):

```bash
sudo python3 therescreen.py --no-audio
```

Sin control de brillo de hardware (solo sonido + visual web):

```bash
sudo python3 therescreen.py --no-brightness
```

Sin ambient light:

```bash
sudo python3 therescreen.py --no-ambient
```

Cambiar puerto:

```bash
sudo python3 therescreen.py --port 9000
```

Cambiar archivo de presets:

```bash
sudo python3 therescreen.py --presets-file /ruta/a/mis_presets.yaml
```

Si ves `Address already in use`, usa otro puerto:

```bash
sudo python3 therescreen.py --port 8766
```

Logs detallados (archivo + stderr):

```bash
sudo python3 therescreen.py --log-level DEBUG
tail -f therescreen.log
```

Cambiar ruta de log:

```bash
sudo python3 therescreen.py --log-file /tmp/therescreen.log
```

## Presets de sintetizador (YAML)

El archivo `synth_presets.yaml` contiene presets de sintetizador con todos los parametros `synth`.

Ejemplo de estructura:

```yaml
mi_preset:
  waveform: sine
  minHz: 130.81
  maxHz: 1760.0
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
  lowVolume: 0.03
  highVolume: 0.66
  masterGain: 0.9
```

Desde UI (`Sonido`):

1. Elegis preset en dropdown y tocas `Cargar preset`.
2. Ajustas sliders.
3. Escribis nombre nuevo y `Guardar preset`.
4. Si editaste el YAML manualmente, `Recargar YAML`.

## Notas

- Si `lid-angle` o `ambient-light` fallan por permisos, ejecuta la app con `sudo`.
- Para menor latencia se intenta lectura directa SPU HID; si falla, cae a comandos `lid-angle --json` / `ambient-light --json`.
- Si el puerto esta ocupado, la app intenta limpiar procesos viejos de `therescreen.py` y reintenta bind automaticamente.
- La configuracion se guarda en `therescreen_config.json`.
- Logs con rotacion en `therescreen.log` (2MB x 5 archivos).
- Los comandos de hardware se pueden cambiar por flags:
- `--lid-command`
- `--ambient-command`
- `--speaker-command`
- `--keyboard-set-command`
- `--screen-set-command`
- `--presets-file`
- `--log-file`
- `--log-level`
