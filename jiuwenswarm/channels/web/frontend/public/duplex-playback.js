class DuplexPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.lanes = {
      normal: this.createLane(),
      urgent: this.createLane(),
    };
    this.paused = false;
    this.port.onmessage = ({ data }) => {
      if (data.type === 'clear') {
        const responseId = this.lanes.urgent.responseId || this.lanes.normal.responseId;
        const playedSamples = this.lanes.urgent.playedSamples + this.lanes.normal.playedSamples;
        this.port.postMessage({
          type: 'cleared',
          responseId,
          playedMs: Math.round(playedSamples * 1000 / sampleRate),
          cancelResponse: data.cancelResponse !== false,
        });
        this.lanes.normal = this.createLane();
        this.lanes.urgent = this.createLane();
        this.paused = false;
      } else if (data.type === 'pause') {
        this.paused = true;
      } else if (data.type === 'resume') {
        this.paused = false;
      } else if (data.type === 'audio') {
        const lane = this.lane(data.lane);
        lane.queue.push(new Int16Array(data.pcm));
        lane.responseId = data.responseId || lane.responseId;
      } else if (data.type === 'drain') {
        this.lane(data.lane).drainRequested = true;
      }
    };
  }

  createLane() {
    return {
      queue: [],
      offset: 0,
      playedSamples: 0,
      responseId: null,
      drainRequested: false,
      playing: false,
    };
  }

  lane(name) {
    return name === 'urgent' ? this.lanes.urgent : this.lanes.normal;
  }

  hasPending() {
    return ['urgent', 'normal'].some((name) => {
      const lane = this.lanes[name];
      return lane.queue.length > 0 || lane.playing;
    });
  }

  reportDrained(name) {
    const lane = this.lanes[name];
    lane.playing = false;
    this.port.postMessage({
      type: 'drained',
      lane: name,
      responseId: lane.responseId,
      playedMs: Math.round(lane.playedSamples * 1000 / sampleRate),
      hasPending: this.hasPending(),
    });
    this.lanes[name] = this.createLane();
  }

  selectedLane() {
    const urgent = this.lanes.urgent;
    if (urgent.queue.length > 0 || urgent.playing) return 'urgent';
    const normal = this.lanes.normal;
    if (normal.queue.length > 0 || normal.playing) return 'normal';
    return null;
  }

  process(_inputs, outputs) {
    const output = outputs[0]?.[0];
    if (!output) return true;
    if (this.paused) return true;
    const name = this.selectedLane();
    if (!name) return true;
    const lane = this.lanes[name];
    if (!lane.playing && lane.queue.length > 0) {
      const buffered = lane.queue.reduce((total, chunk) => total + chunk.length, -lane.offset);
      const startupSamples = sampleRate * (name === 'urgent' ? 0.15 : 0.8);
      if (!lane.drainRequested && buffered < startupSamples) return true;
      lane.playing = true;
    }
    let target = 0;
    while (target < output.length && lane.queue.length > 0) {
      const chunk = lane.queue[0];
      const count = Math.min(output.length - target, chunk.length - lane.offset);
      for (let index = 0; index < count; index += 1) {
        output[target + index] = chunk[lane.offset + index] / 32768;
      }
      target += count;
      lane.offset += count;
      lane.playedSamples += count;
      if (lane.offset === chunk.length) {
        lane.queue.shift();
        lane.offset = 0;
      }
    }
    if (lane.drainRequested && lane.queue.length === 0) this.reportDrained(name);
    return true;
  }
}

registerProcessor('jiuwen-duplex-playback', DuplexPlaybackProcessor);
