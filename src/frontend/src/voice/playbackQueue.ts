/**
 * Cola de reproducción de audio (muestras Float32 mono). Es la misma lógica del worklet
 * `public/worklets/pcm16-playback.js`, aquí como clase pura para poder probarla:
 * `push` agrega audio, `clear` la vacía al instante (barge-in o bloqueo del Guardrail) y `pull` entrega el
 * siguiente bloque, completando con silencio si no hay audio.
 */
export class PlaybackQueue {
  private queue: Float32Array[] = [];
  private offset = 0;

  push(samples: Float32Array): void {
    if (samples.length > 0) this.queue.push(samples);
  }

  /** Vacía la cola: después de esto ningún audio anterior vuelve a sonar. */
  clear(): void {
    this.queue = [];
    this.offset = 0;
  }

  get pending(): number {
    let total = 0;
    for (const chunk of this.queue) total += chunk.length;
    return total - this.offset;
  }

  get playing(): boolean {
    return this.queue.length > 0;
  }

  pull(size: number): Float32Array {
    const out = new Float32Array(size);
    let i = 0;
    while (i < size && this.queue.length > 0) {
      const head = this.queue[0]!;
      const n = Math.min(size - i, head.length - this.offset);
      out.set(head.subarray(this.offset, this.offset + n), i);
      i += n;
      this.offset += n;
      if (this.offset >= head.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    return out;
  }
}
