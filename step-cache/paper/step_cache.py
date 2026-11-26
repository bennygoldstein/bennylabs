"""Fixed-schedule step cache for video diffusion transformers.

Companion code to "Skip a Step, Keep the Clip" (B. Goldstein, Benny Labs Foundation, 2026).
https://bennylabs.org/step-cache/paper/

On a fixed schedule the whole layer stack is skipped, and the one change it made the last
time it ran (last layer's output minus first layer's input) is added to the new input.
The first and last steps of a run always run in full.

Tested with Diffusers and Wan 2.1 T2V-14B (40 layers, 30 steps, guidance on, so the
transformer is called twice per step: once with the prompt and once without).

    pipe = WanPipeline.from_pretrained(...)
    cache = StepCache(pipe.transformer, every=2)      # every=3 reuses two of every three steps
    cache.reset()                                     # call once before each clip
    video = pipe(prompt, num_inference_steps=30, guidance_scale=5.0).frames[0]
    cache.remove()                                    # give the model back unchanged

Schedule (0-based step numbers): steps before `start` and from `stop` on always run in full.
Inside the window a step runs in full when (step - start) % every == 0 and is reused otherwise.
The defaults are the ones measured in the paper for a 30-step run. For another step count,
keep about the first sixth and the last eighth of the run fresh.

Free to use, test and build on.
"""


class StepCache:
    def __init__(self, transformer, every=2, start=5, stop=26, passes=2):
        self.every, self.start, self.stop, self.passes = every, start, stop, passes
        self.blocks = list(transformer.blocks)
        self.orig = [b.forward for b in self.blocks]
        for i, b in enumerate(self.blocks):
            b.forward = self._wrap(i)
        self.hook = transformer.register_forward_pre_hook(self._tick)
        self.reset()

    def reset(self):
        """Call before every clip."""
        self.calls = 0                      # transformer calls so far in this clip
        self.saved = [None] * self.passes   # one stored change per pass (with prompt, without prompt)
        self.skipped = 0                    # stack runs skipped, for reporting
        self.p, self.reuse, self.inp = 0, False, None

    def _tick(self, module, args):
        step, self.p = divmod(self.calls, self.passes)
        self.calls += 1
        self.reuse = self.start <= step < self.stop and (step - self.start) % self.every != 0
        if self.reuse and self.saved[self.p] is not None:
            self.skipped += 1

    def _wrap(self, i):
        orig, last = self.orig[i], len(self.blocks) - 1

        def forward(*a, **k):
            h = k["hidden_states"] if "hidden_states" in k else a[0]
            if self.reuse and self.saved[self.p] is not None:
                return h + self.saved[self.p] if i == last else h    # skip the stack, add the stored change at the end
            if i == 0:
                self.inp = h
            out = orig(*a, **k)
            if i == last:
                self.saved[self.p] = out - self.inp                 # the change the whole stack made
            return out

        return forward

    def remove(self):
        for b, f in zip(self.blocks, self.orig):
            b.forward = f
        self.hook.remove()
