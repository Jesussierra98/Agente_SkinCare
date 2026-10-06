/**
 * Reproducción: cola de muestras Float32 (mono). `push` agrega audio, `clear` la vacía al instante
 * (barge-in / bloqueo). Cuando no hay audio reproduce silencio.
 */
class Pcm16Playback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.playing = false;
    this.port.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === 'push') {
        this.queue.push(msg.samples);
      } else if (msg.type === 'clear') {
        this.queue = [];
        this.offset = 0;
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    let i = 0;
    while (i < out.length && this.queue.length > 0) {
      const head = this.queue[0];
      const n = Math.min(out.length - i, head.length - this.offset);
      out.set(head.subarray(this.offset, this.offset + n), i);
      i += n;
      this.offset += n;
      if (this.offset >= head.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    for (; i < out.length; i++) out[i] = 0;
    const isPlaying = this.queue.length > 0;
    if (isPlaying !== this.playing) {
      this.playing = isPlaying;
      this.port.postMessage({ type: 'state', playing: isPlaying });
    }
    return true;
  }
}

registerProcessor('pcm16-playback', Pcm16Playback);
