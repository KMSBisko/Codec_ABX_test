// Execute the real client in a VM with fake DOM/Web Audio/network boundaries.
// These regressions prove source/buffer ownership, NOT audible playback quality.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const client = fs.readFileSync(
    path.join(__dirname, '../../web/static/js/app.js'), 'utf8',
);

function harness({ kind = 'normal', mapping = { A: 'B', B: 'A' },
                   identical = false, runOver = false, holdX = false } = {}) {
    const elements = new Map();
    function element(id) {
        if (!elements.has(id)) {
            const classes = new Set();
            elements.set(id, {
                value: id === 'loopEnd' ? '10' : '0', checked: false,
                disabled: false, textContent: '', title: '',
                classList: {
                    add: (...names) => names.forEach(name => classes.add(name)),
                    remove: (...names) => names.forEach(name => classes.delete(name)),
                    toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
                    contains: name => classes.has(name),
                },
                matches: () => false,
            });
        }
        return elements.get(id);
    }
    const buffers = Object.fromEntries(
        ['old-a', 'old-b', 'old-x', 'next-a', 'next-b', 'next-x'].map(
            name => [name, { name, duration: 10 }],
        ),
    );
    const sources = [];
    const audio = {
        currentTime: 1, sampleRate: 48000, state: 'running', destination: {},
        createBufferSource() {
            const source = {
                connect() {},
                start(when, offset) { this.offset = offset; },
                stop(when) { this.stoppedAt = when; },
            };
            sources.push(source);
            return source;
        },
        createGain() {
            return { connect() {}, gain: {
                value: 1, cancelScheduledValues() {}, setValueAtTime() {},
                linearRampToValueAtTime() {},
            } };
        },
        async decodeAudioData(bytes) {
            return buffers[new TextDecoder().decode(bytes)];
        },
    };
    let releaseX;
    const xGate = new Promise(resolve => { releaseX = resolve; });
    let markXRequested;
    const xRequested = new Promise(resolve => { markXRequested = resolve; });
    const calls = [];
    const errors = [];
    const stats = {
        total_trials: 1, correct_trials: 1, p_value_one_tailed: 0.5,
        planned_trials: 4, required_correct: 4, gates: {},
        current_trial_kind: kind, can_accept_answers: !runOver,
        lifecycle: runOver ? 'completed' : 'running',
    };
    const context = vm.createContext({
        document: { addEventListener() {}, getElementById: element },
        window: {},
        console: { error: (...args) => errors.push(args), log() {} },
        alert: message => { throw new Error(message); },
        requestAnimationFrame: () => 1, cancelAnimationFrame() {},
        async fetch(url) {
            calls.push(url);
            if (url === '/api/trial/answer') {
                return { ok: true, json: async () => ({
                    trial: { correct: true, x_is: 'A', kind: 'normal' }, stats,
                }) };
            }
            if (url === '/api/trial/stats') {
                return { ok: true, json: async () => stats };
            }
            if (url === '/api/trial/start') {
                return { ok: true, json: async () => ({
                    token: 'next-token', display_mapping: mapping,
                }) };
            }
            const match = /^\/api\/audio\/([abx])\?token=next-token$/.exec(url);
            assert.ok(match, `unexpected request ${url}`);
            const side = match[1];
            if (side === 'x') {
                markXRequested();
                if (holdX) await xGate;
            }
            return { ok: true, arrayBuffer: async () =>
                new TextEncoder().encode(`next-${side}`).buffer };
        },
    });
    vm.runInContext(client + '\n;globalThis.clientState = state;', context);
    const state = context.clientState;
    Object.assign(state, {
        audioContext: audio, audioDuration: 10, preparedAudioId: 'pair-1',
        bufferA: buffers['old-a'], bufferB: buffers['old-b'], bufferX: buffers['old-x'],
        xToken: 'old-token', buffersIdenticalFromServer: identical,
        bufferCacheKey: 'pair-1|pair|AB',
    });
    return { context, state, buffers, sources, audio, element, calls, errors,
             xRequested, releaseX };
}

for (const side of ['b', 'x']) {
    test(`live A→${side.toUpperCase()} keeps selected side after scrub`, () => {
        const h = harness();
        h.context.togglePlayPause();
        h.context.switchSide(side);
        assert.equal(h.state.currentSource.buffer, h.buffers[`old-${side}`]);
        h.element('scrubSlider').value = '25';
        h.context.handleScrub();
        assert.equal(h.state.currentSource.buffer, h.buffers[`old-${side}`]);
        assert.equal(h.state.currentSide, side);
        assert.equal(h.state.currentSource.offset, 2.5);
    });

    test(`live A→${side.toUpperCase()} keeps selected side after pause/resume`, () => {
        const h = harness();
        h.context.togglePlayPause();
        h.context.switchSide(side);
        h.audio.currentTime += 0.75;
        h.context.togglePlayPause();
        assert.equal(h.state.isPaused, true);
        h.context.togglePlayPause();
        assert.equal(h.state.currentSource.buffer, h.buffers[`old-${side}`]);
        assert.equal(h.state.currentSide, side);
        assert.equal(h.state.currentSource.offset, 0.75);
    });
}

for (const side of ['a', 'b', 'x']) {
    test(`auto-advance rebinds playing ${side.toUpperCase()} to next trial's bytes`, async () => {
        const h = harness();
        h.context.switchSide(side);
        h.context.togglePlayPause();
        const previous = h.state.currentSource;
        h.audio.currentTime += 2.25;
        await h.context.submitAnswer('A');
        assert.equal(h.state.xToken, 'next-token');
        assert.equal(h.state.currentSide, side);
        assert.equal(h.state.currentSource.buffer, h.buffers[`next-${side}`]);
        assert.notEqual(h.state.currentSource, previous);
        assert.equal(h.state.currentSource.offset, 2.25);
        // Superseded source end events must not stop the replacement.
        previous.onended();
        assert.equal(h.state.isPlaying, true);
        assert.equal(h.state.currentSource.buffer, h.buffers[`next-${side}`]);
    });
}

test('canary→identical normal pair rebinds live audio to shared normal PCM', async () => {
    const h = harness({ identical: true });
    h.state.canaryTrial = true;
    h.state.currentTrialKind = 'canary';
    h.state.bufferCacheKey = 'pair-1|cal|AB';
    h.context.switchSide('b');
    h.context.togglePlayPause();
    await h.context.submitAnswer('A');
    assert.equal(h.state.canaryTrial, false);
    assert.equal(h.state.buffersIdentical, true);
    assert.equal(h.state.currentSource.buffer, h.buffers['next-a']);
    assert.equal(h.state.bufferB, h.state.bufferA);
});

test('manual next trial also rebinds live X after loading', async () => {
    const h = harness();
    h.context.switchSide('x');
    h.context.togglePlayPause();
    await h.context.startNewTrial();
    assert.equal(h.state.currentSource.buffer, h.buffers['next-x']);
});

test('handoff wraps the timeline within the loop region', async () => {
    const h = harness();
    h.element('loopEnabled').checked = true;
    h.element('loopStart').value = '2';
    h.element('loopEnd').value = '4';
    h.context.switchSide('x');
    h.context.togglePlayPause();
    h.audio.currentTime += 5.5;
    await h.context.submitAnswer('A');
    assert.equal(h.state.currentSource.buffer, h.buffers['next-x']);
    assert.equal(h.state.currentSource.offset, 3.5);
    assert.equal(h.state.currentSource.loopStart, 2);
    assert.equal(h.state.currentSource.loopEnd, 4);
});

test('slow X loading completes before the playing source is rebound', async () => {
    const h = harness({ holdX: true });
    h.context.switchSide('x');
    h.context.togglePlayPause();
    const previous = h.state.currentSource;
    const transition = h.context.submitAnswer('A');
    await h.xRequested;
    assert.equal(h.state.currentSource, previous);
    assert.equal(h.state.currentSource.buffer, h.buffers['old-x']);
    assert.equal(h.sources.length, 1);
    h.audio.currentTime += 3;
    h.releaseX();
    await transition;
    assert.equal(h.sources.length, 2);
    assert.equal(h.state.currentSource.buffer, h.buffers['next-x']);
    assert.equal(h.state.currentSource.offset, 3);
});

test('pausing during a slow handoff is respected after decoding finishes', async () => {
    const h = harness({ holdX: true });
    h.context.switchSide('x');
    h.context.togglePlayPause();
    h.audio.currentTime += 1.5;
    const transition = h.context.submitAnswer('A');
    await h.xRequested;
    h.context.togglePlayPause();
    const count = h.sources.length;
    h.releaseX();
    await transition;
    assert.equal(h.state.isPlaying, false);
    assert.equal(h.state.isPaused, true);
    assert.equal(h.sources.length, count);
    h.context.togglePlayPause();
    assert.equal(h.state.currentSource.buffer, h.buffers['next-x']);
    assert.equal(h.state.currentSource.offset, 1.5);
});

test('fixed mapping reuses A/B cache but still rebinds X to the next token', async () => {
    const h = harness({ mapping: { A: 'A', B: 'B' } });
    h.context.switchSide('x');
    h.context.togglePlayPause();
    await h.context.submitAnswer('A');
    assert.equal(h.state.bufferA, h.buffers['old-a']);
    assert.equal(h.state.bufferB, h.buffers['old-b']);
    assert.equal(h.calls.some(url => /^\/api\/audio\/[ab]\?/.test(url)), false);
    assert.equal(h.state.currentSource.buffer, h.buffers['next-x']);
});

test('paused next-trial load does not resume playback', async () => {
    const h = harness();
    h.context.switchSide('x');
    h.context.togglePlayPause();
    h.audio.currentTime += 1.5;
    h.context.togglePlayPause();
    const count = h.sources.length;
    await h.context.startNewTrial();
    assert.equal(h.state.isPlaying, false);
    assert.equal(h.state.isPaused, true);
    assert.equal(h.sources.length, count);
    h.context.togglePlayPause();
    assert.equal(h.state.currentSource.buffer, h.buffers['next-x']);
    assert.equal(h.state.currentSource.offset, 1.5);
});

test('idle next-trial load does not start playback', async () => {
    const h = harness();
    await h.context.startNewTrial();
    assert.equal(h.state.isPlaying, false);
    assert.equal(h.sources.length, 0);
});

test('reaching N never starts another trial or replaces playback', async () => {
    const h = harness({ runOver: true });
    h.context.switchSide('x');
    h.context.togglePlayPause();
    const previous = h.state.currentSource;
    await h.context.submitAnswer('A');
    assert.equal(h.calls.includes('/api/trial/start'), false);
    assert.equal(h.state.currentSource, previous);
    assert.equal(h.element('btnAnswerA').disabled, true);
});
