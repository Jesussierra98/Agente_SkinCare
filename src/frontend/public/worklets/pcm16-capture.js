/**
 * Captura de micrófono: remuestrea a `targetRate` (16 kHz) por interpolación lineal y entrega
 * marcos PCM16 mono al hilo principal. Funciona con cualquier frecuencia del AudioContext.
 */
class Pcm16Capture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.ratio = sampleRate / (opts.targetRate || 16000);
    this.frameSamples = opts.frameSamples || 1024; // ~64 ms a 16 kHz
    this.out = new Int16Array(this.frameSamples);
    this.n = 0;
    this.pos = 0; // posición de lectura relativa al bloque actual (puede ser -1..0)
    this.prev = 0; // último muestreo del bloque anterior
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    const len = channel.length;
    let pos = this.pos;
    while (Math.floor(pos) + 1 < len) {
      let a;
      let b;
      let frac;
      if (pos < 0) {
        a = this.prev;
        b = channel[0];
        frac = pos + 1;
      } else {
        const i = Math.floor(pos);
        a = channel[i];
        b = channel[i + 1];
        frac = pos - i;
      }
      const s = Math.max(-1, Math.min(1, a + (b - a) * frac));
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.frameSamples) {
        const copy = this.out.slice();
        this.port.postMessage(copy.buffer, [copy.buffer]);
        this.n = 0;
      }
      pos += this.ratio;
    }
    this.pos = pos - len;
    this.prev = channel[len - 1];
    return true;
  }
}

registerProcessor('pcm16-capture', Pcm16Capture);
