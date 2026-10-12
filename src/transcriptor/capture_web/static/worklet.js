// Envía bloques de ~100 ms de audio mono float32 (el AudioContext ya trabaja a 16 kHz).
class PcmSender extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(1600);
    this.pos = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;
    const channels = input.length;
    const frames = input[0].length;
    for (let i = 0; i < frames; i++) {
      let sum = 0;
      for (let c = 0; c < channels; c++) sum += input[c][i];
      this.buffer[this.pos++] = sum / channels;
      if (this.pos === this.buffer.length) {
        let rms = 0;
        for (let j = 0; j < this.buffer.length; j++) rms += this.buffer[j] * this.buffer[j];
        rms = Math.sqrt(rms / this.buffer.length);
        this.port.postMessage({ buffer: this.buffer.slice(0), rms });
        this.pos = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-sender", PcmSender);
