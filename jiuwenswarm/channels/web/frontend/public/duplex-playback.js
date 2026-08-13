class DuplexPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.playedSamples = 0;
    this.responseId = null;
    this.drainRequested = false;
    this.playing = false;
    this.port.onmessage = ({ data }) => {
      if (data.type === 'clear') {
        this.port.postMessage({
          type: 'cleared',
          responseId: this.responseId,
          playedMs: Math.round(this.playedSamples * 1000 / sampleRate),
          cancelResponse: data.cancelResponse !== false,
        });
        this.queue = [];
        this.offset = 0;
        this.playedSamples = 0;
        this.responseId = null;
        this.drainRequested = false;
        this.playing = false;
      } else if (data.type === 'audio') {
        this.queue.push(new Int16Array(data.pcm));
        this.responseId = data.responseId || this.responseId;
      } else if (data.type === 'drain') {
        this.drainRequested = true;
        if (this.queue.length === 0) this.reportDrained();
      }
    };
  }

  reportDrained() {
    this.port.postMessage({
      type: 'drained',
      responseId: this.responseId,
      playedMs: Math.round(this.playedSamples * 1000 / sampleRate),
    });
    this.playedSamples = 0;
    this.responseId = null;
    this.drainRequested = false;
    this.playing = false;
  }

  process(_inputs, outputs) {
    const output = outputs[0]?.[0];
    if (!output) return true;
    if (!this.playing && this.queue.length > 0) {
      const buffered = this.queue.reduce((total, chunk) => total + chunk.length, -this.offset);
      if (!this.drainRequested && buffered < sampleRate * 0.8) return true;
      this.playing = true;
    }
    let target = 0;
    while (target < output.length && this.queue.length > 0) {
      const chunk = this.queue[0];
      const count = Math.min(output.length - target, chunk.length - this.offset);
      for (let index = 0; index < count; index += 1) {
        output[target + index] = chunk[this.offset + index] / 32768;
      }
      target += count;
      this.offset += count;
      this.playedSamples += count;
      if (this.offset === chunk.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    // Keep the playback clock running across short network underflows. Resetting
    // to the startup buffer here turned every small jitter into a 120ms pause.
    if (this.drainRequested && this.queue.length === 0) this.reportDrained();
    return true;
  }
}

registerProcessor('jiuwen-duplex-playback', DuplexPlaybackProcessor);
