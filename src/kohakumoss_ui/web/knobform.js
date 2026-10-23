/* NEW RUN knob workspace: real trainer sections, three levels, search, help.
   All field names and data-knob values stay exactly as scanned from the trainer. */

import { esc } from "./format.js";

const ENUMS = {
  VARIANT: ["base", "sft"],
  ALGO: ["lora", "locon", "loha", "lokr", "oft", "boft", "glora", "ia3", "dylora", "tlora", "full"],
  RESOLUTION_MODE: ["resize", "bucket", "shifted_square", "square_crop"],
  BATCHING: ["tokens", "tokens_ffd", "count"],
  OVERLONG: ["drop", "truncate_text", "drop_tokens"],
  TOKEN_BUDGET: ["estimate", "render", "exact"],
  LATENT_CACHE: ["off", "offline", "online"],
  TEXT_CACHE: ["off", "offline", "online"],
  PRECISION: ["bf16", "fp16", "auto"],
  FP16_FIX: ["none", "online", "fp32_residual"],
  OPTIMIZER: ["adamw", "adamw16", "adamw8bit", "adamw8bit_kernel", "muon", "prodigy", "schedule_free"],
  WD_RULES: ["moss", "none", "all"],
  TIMESTEP: ["moss", "uniform", "logit_normal"],
  LOSS: ["mse", "huber", "l1"],
  WEIGHTING: ["none", "x0", "eps"],
  ADAPTER_MODE: ["bypass", "merge"],
  MEMORY_PROFILE: ["speed", "balanced", "low", "minimal", "auto", "manual"],
  PARALLEL: ["none", "ddp", "fsdp", "pipeline", "threaded"],
  SAVE_FORMAT: ["comfy", "hf", "both", "none"],
  LOGGER: ["console", "wandb"],
  DRIVER: ["native", "lightning"],
  CROP: ["center", "random"],
  UPSCALE: ["never", "to_area"],
};

const BASE_NAMES = new Set([
  "WEIGHTS", "VARIANT", "TEXT_ENCODER", "VAE", "VAE_DTYPE", "TRAIN_TEXT_REFINER",
  "DATASETS", "RESOLUTION_MODE", "TARGET_AREA", "BATCHING", "MAX_TOKENS", "OVERLONG",
  "LATENT_CACHE", "TEXT_CACHE", "CAPTION", "SEED",
  "ALGO", "PRESET", "DIM", "ALPHA", "DORA", "ADAPTER_MODE",
  "TIMESTEP", "LOSS", "WEIGHTING",
  "OPTIMIZER", "LR", "BETAS", "WEIGHT_DECAY", "SCHEDULE", "GRAD_CLIP",
  "PRECISION", "MEMORY_PROFILE",
  "STEPS", "GRAD_ACC", "OUTPUT_DIR",
  "SAVE_EVERY", "SAVE_MINUTES", "SAVE_FORMAT",
  "SAMPLE_PROMPTS", "SAMPLE_EVERY", "SAMPLE_STEPS",
]);

const MEMORY_MANUAL = [
  "CHECKPOINT", "STREAM", "STREAM_RESIDENT", "PINNED",
  "ACT_OFFLOAD", "OPTIM_STATE", "GRAD_OFFLOAD", "FUSED_STEP",
];

const EXPERT_NAMES = new Set([
  ...MEMORY_MANUAL,
  "MEMORY_OVERRIDES",
  "ARCH_OVERRIDES", "TEXT_ENCODE", "VAE_ENCODE", "PLANNER_KWARGS",
  "KERNELS", "TUNE", "RETUNE_STEPS",
  "TRACE_STEPS", "PROFILE_STEPS",
  "DIST_BACKEND", "FSDP_REDUCE", "FSDP_RESHARD", "DDP_BACKEND", "DDP_REDUCE_DTYPE",
  "DIST_TIMEOUT", "DIST_GATE_TIMEOUT", "DIST_BIND",
  "EXPORT_FORMATS", "VALIDATION",
  "ALGO_KWARGS", "TIMESTEP_KWARGS", "LOSS_KWARGS", "OPTIMIZER_KWARGS", "FP16_FIX_KWARGS",
]);

const EXPERT_SECTIONS = new Set(["KERNELS"]);
const TEXT_BOOLEANS = new Set(["DATA_RESCAN"]);

const LABELS = {
  WEIGHTS: "Model weights",
  VARIANT: "Model variant",
  ARCH_OVERRIDES: "Architecture overrides",
  TRANSFORMER: "Transformer path",
  TEXT_ENCODER: "Text encoder",
  TEXT_DEVICE: "Text encoder device",
  TEXT_ENCODE: "Text encoder plan",
  VAE: "VAE",
  VAE_ENCODE: "VAE plan",
  VAE_DTYPE: "VAE precision",
  TRAIN_TEXT_REFINER: "Train text refiner",
  DATASETS: "Datasets",
  RESOLUTION_MODE: "Resolution mode",
  TARGET_AREA: "Target pixel area",
  SIZE_STEP: "Pixel size step",
  ASPECT_RANGE: "Aspect ratio range",
  UPSCALE: "Upscale mode",
  BATCHING: "Batching mode",
  MAX_TOKENS: "Max joint tokens",
  OVERLONG: "Overlong samples",
  BATCH_SIZE: "Samples per batch",
  MAX_SAMPLES: "Max samples per batch",
  TOKEN_BUDGET: "Token budget mode",
  CAPTION: "Caption template",
  TEXT_DROP: "Caption dropout",
  LATENT_CACHE: "Latent cache",
  TEXT_CACHE: "Text cache",
  CACHE_DIR: "Cache directory",
  CACHE_DTYPE: "Cache precision",
  CAPTION_VARIANTS: "Caption variants",
  FLIP: "Horizontal flip",
  CROP: "Crop mode",
  PLANNER_KWARGS: "Planner kwargs",
  LATENT_ENCODER_ID: "Latent encoder ID",
  LATENT_DRAW: "Latent draw mode",
  TEXT_ENCODER_ID: "Text encoder ID",
  BACKGROUND: "Transparent fill",
  RESAMPLE: "Resize filter",
  TEXT_CHARS_PER_TOKEN: "Caption chars per token",
  TEXT_OVERHEAD_TOKENS: "Caption overhead tokens",
  MAX_TEXT_TOKENS: "Max caption tokens",
  PREFETCH: "Prefetch batches",
  PIN_MEMORY: "Pin host memory",
  NUM_WORKERS: "Loader workers",
  DATA_RESCAN: "Catalog rescan",
  DATA_SCAN_WORKERS: "Scan workers",
  DATA_WAIT: "Catalog wait seconds",
  SEED: "Random seed",
  ALGO: "Adapter algorithm",
  PRESET: "Target preset",
  DIM: "Adapter rank",
  ALPHA: "Adapter alpha",
  ALGO_KWARGS: "Adapter kwargs",
  DORA: "DoRA",
  DORA_DETACH: "Detach DoRA norm",
  DROPOUT: "Adapter dropout",
  ADAPTER_MODE: "Adapter merge mode",
  LORAPLUS_RATIO: "LoRA+ learning-rate ratio",
  TRAIN_NORMS: "Train normalization layers",
  TIMESTEP: "Timestep sampling",
  TIMESTEP_KWARGS: "Timestep kwargs",
  LOSS: "Loss function",
  LOSS_KWARGS: "Loss kwargs",
  WEIGHTING: "Loss weighting",
  OPTIMIZER: "Optimizer",
  OPTIMIZER_KWARGS: "Optimizer kwargs",
  LR: "Learning rate",
  BETAS: "Adam betas",
  EPS: "Adam epsilon",
  WEIGHT_DECAY: "Weight decay",
  WD_RULES: "Weight-decay rules",
  SCHEDULE: "LR schedule",
  GRAD_CLIP: "Gradient clip",
  EMA: "EMA decay",
  PRECISION: "Training precision",
  KEEP_FP32: "Keep modules in fp32",
  FP16_FIX: "FP16 fix",
  FP16_FIX_KWARGS: "FP16 fix kwargs",
  MEMORY_PROFILE: "Memory profile",
  MEMORY_AUTO_FORCE: "Auto profile fallback",
  STEP_RELEASE: "Step memory release",
  CHECKPOINT: "Manual checkpoint mode",
  STREAM: "Manual block streaming",
  STREAM_RESIDENT: "Stream resident blocks",
  PINNED: "Pinned host memory",
  ACT_OFFLOAD: "Activation offload",
  OPTIM_STATE: "Optimizer state placement",
  GRAD_OFFLOAD: "Gradient offload",
  FUSED_STEP: "Fused optimizer step",
  BASE_PRECISION: "Base model precision",
  FP8_RECIPE: "FP8 scaling recipe",
  FP8_DGRAD: "FP8 input gradient",
  MEMORY_OVERRIDES: "Memory plan overrides",
  PARALLEL: "Parallel mode",
  DIST_BACKEND: "Distributed backend",
  DEVICES: "Devices",
  PIPELINE_SCHEDULE: "Pipeline schedule",
  PIPELINE_SPLIT: "Pipeline split",
  MICROBATCHES: "Pipeline microbatches",
  FSDP_REDUCE: "FSDP reduce timing",
  FSDP_RESHARD: "FSDP reshard policy",
  DDP_BACKEND: "DDP backend",
  DDP_REDUCE_DTYPE: "DDP reduce dtype",
  DIST_TIMEOUT: "Distributed timeout",
  DIST_GATE_TIMEOUT: "Distributed gate timeout",
  DIST_BIND: "Communicator binding",
  KERNELS: "Kernel overrides",
  TUNE: "Kernel tuning",
  RETUNE_STEPS: "Retune steps",
  COMPILE: "Compile mode",
  DRIVER: "Training driver",
  STEPS: "Training steps",
  GRAD_ACC: "Gradient accumulation",
  OUTPUT_DIR: "Output directory",
  LOGGER: "Logger",
  PROJECT: "Project name",
  LOG_EVERY: "Log every N steps",
  METRICS_EVERY: "Metrics every N steps",
  PROGRESS: "Progress bar",
  NONFINITE_PATIENCE: "Non-finite patience",
  TRACE_STEPS: "Trace steps",
  PROFILE_STEPS: "Profile steps",
  SAMPLE_PROMPTS: "Sample prompts",
  SAMPLE_NEGATIVE: "Negative prompt",
  SAMPLE_EVERY: "Sample every N steps",
  SAMPLE_STEPS: "Sampler steps",
  SAMPLE_CFG: "Sampler CFG",
  SAMPLE_SHIFT: "Sampler shift",
  SAMPLE_SKIP_LAYERS: "Sampler skip layers",
  SAMPLE_SIZE: "Sample size",
  SAMPLE_SEEDS: "Sample seeds",
  VALIDATE_EVERY: "Validate every N steps",
  VALIDATION: "Validation config",
  SAVE_EVERY: "Save every N steps",
  SAVE_MINUTES: "Save every N minutes",
  KEEP_LAST: "Keep last checkpoints",
  FINAL_CHECKPOINT: "Final checkpoint",
  RESUME: "Resume from",
  SAVE_FORMAT: "Export format",
  SAVE_MERGED: "Save merged model",
  SAVE_FUSED_QKV: "Fused QKV export",
  CHECKPOINT_EXPORT: "Export inside checkpoints",
  EXPORT_FORMATS: "Legacy export formats",
};

const GROUP_META = {
  MODEL: {
    title: "MODEL / WEIGHTS",
    note: "Paths are resolved by the trainer; the environment page checks what exists.",
    subgroups: [
      { title: "WEIGHTS & PATHS", names: ["WEIGHTS", "VARIANT", "TRANSFORMER", "TEXT_ENCODER", "TEXT_DEVICE", "VAE", "VAE_DTYPE", "TRAIN_TEXT_REFINER"] },
      { title: "ARCHITECTURE OVERRIDES", names: ["ARCH_OVERRIDES", "TEXT_ENCODE", "VAE_ENCODE"] },
    ],
  },
  DATA: {
    title: "DATA / CACHE",
    note: "Cache modes and resolution are linked; the inline state explains conflicts.",
    subgroups: [
      { title: "SOURCES & RESOLUTION", names: ["DATASETS", "RESOLUTION_MODE", "TARGET_AREA", "SIZE_STEP", "ASPECT_RANGE", "UPSCALE", "BACKGROUND", "RESAMPLE", "FLIP", "CROP", "SEED"] },
      { title: "BATCHING", names: ["BATCHING", "MAX_TOKENS", "OVERLONG", "BATCH_SIZE", "MAX_SAMPLES", "TOKEN_BUDGET", "TEXT_DROP", "PREFETCH", "PIN_MEMORY", "NUM_WORKERS"] },
      { title: "CACHES", names: ["LATENT_CACHE", "TEXT_CACHE", "CACHE_DIR", "CACHE_DTYPE", "CAPTION_VARIANTS", "LATENT_ENCODER_ID", "LATENT_DRAW", "TEXT_ENCODER_ID"] },
      { title: "CAPTIONS & PLANNING", names: ["CAPTION", "PLANNER_KWARGS", "TEXT_CHARS_PER_TOKEN", "TEXT_OVERHEAD_TOKENS", "MAX_TEXT_TOKENS", "DATA_RESCAN", "DATA_SCAN_WORKERS", "DATA_WAIT"] },
    ],
  },
  ADAPTER: {
    title: "ADAPTER / LoRA",
    note: "Geometry is adapter-only; ALGO=full ignores it.",
    subgroups: [
      { title: "CORE", names: ["ALGO", "PRESET", "DIM", "ALPHA", "DORA", "DORA_DETACH", "DROPOUT", "ADAPTER_MODE"] },
      { title: "ADVANCED", names: ["LORAPLUS_RATIO", "TRAIN_NORMS", "ALGO_KWARGS"] },
    ],
  },
  OBJECTIVE: {
    title: "OBJECTIVE",
    note: "Timestep, loss, and weighting decide what the model learns.",
    subgroups: [
      { title: "TIMESTEP", names: ["TIMESTEP", "TIMESTEP_KWARGS"] },
      { title: "LOSS", names: ["LOSS", "LOSS_KWARGS", "WEIGHTING"] },
    ],
  },
  OPTIM: {
    title: "OPTIMIZER / SCHEDULE",
    note: "Values are passed through; the UI does not change defaults.",
    subgroups: [
      { title: "OPTIMIZER", names: ["OPTIMIZER", "OPTIMIZER_KWARGS", "LR", "BETAS", "EPS", "WEIGHT_DECAY", "WD_RULES", "SCHEDULE", "GRAD_CLIP", "EMA"] },
    ],
  },
  PRECISION: {
    title: "PRECISION",
    note: "Training precision and fp16 fix; base quantization lives in MEMORY.",
    subgroups: [
      { title: "TRAINING", names: ["PRECISION", "KEEP_FP32", "FP16_FIX", "FP16_FIX_KWARGS"] },
    ],
  },
  MEMORY: {
    title: "MEMORY / PLAN",
    note: "Manual plan fields require MEMORY_PROFILE=manual.",
    subgroups: [
      { title: "PROFILE", names: ["MEMORY_PROFILE", "MEMORY_AUTO_FORCE", "STEP_RELEASE"] },
      { title: "MANUAL PLAN", names: MEMORY_MANUAL, note: "Only with MEMORY_PROFILE=manual." },
      { title: "BASE PRECISION", names: ["BASE_PRECISION", "FP8_RECIPE", "FP8_DGRAD"] },
      { title: "PLAN OVERRIDES", names: ["MEMORY_OVERRIDES"] },
    ],
  },
  PARALLEL: {
    title: "PARALLEL",
    note: "Topology changes need matching pipeline and token-budget values.",
    subgroups: [
      { title: "TOPOLOGY", names: ["PARALLEL", "DIST_BACKEND", "DEVICES"] },
      { title: "PIPELINE", names: ["PIPELINE_SCHEDULE", "PIPELINE_SPLIT", "MICROBATCHES"] },
      { title: "DISTRIBUTION", names: ["FSDP_REDUCE", "FSDP_RESHARD", "DDP_BACKEND", "DDP_REDUCE_DTYPE", "DIST_TIMEOUT", "DIST_GATE_TIMEOUT", "DIST_BIND"] },
    ],
  },
  KERNELS: {
    title: "KERNELS",
    note: "Performance tuning; the trainer checks unsafe combinations.",
    subgroups: [
      { title: "TUNING", names: ["KERNELS", "TUNE", "RETUNE_STEPS", "COMPILE"] },
    ],
  },
  RUNTIME: {
    title: "RUNTIME / LOOP",
    note: "RUN_NAME is set above and is part of the run directory contract.",
    subgroups: [
      { title: "LOOP", names: ["DRIVER", "STEPS", "GRAD_ACC"] },
      { title: "OUTPUT", names: ["OUTPUT_DIR"] },
    ],
  },
  LOGGING: {
    title: "LOGGING",
    note: "Console and W&B; trace and profile are diagnostic.",
    subgroups: [
      { title: "DESTINATION", names: ["LOGGER", "PROJECT"] },
      { title: "CADENCE / DIAGNOSTICS", names: ["LOG_EVERY", "METRICS_EVERY", "PROGRESS", "NONFINITE_PATIENCE", "TRACE_STEPS", "PROFILE_STEPS"] },
    ],
  },
  SAMPLING: {
    title: "SAMPLING",
    note: "SAMPLE_EVERY=0 turns sampling off; unused fields are marked.",
    subgroups: [
      { title: "PROMPTS", names: ["SAMPLE_PROMPTS", "SAMPLE_NEGATIVE", "SAMPLE_CFG", "SAMPLE_SHIFT", "SAMPLE_SKIP_LAYERS", "SAMPLE_SIZE", "SAMPLE_SEEDS"] },
      { title: "CADENCE", names: ["SAMPLE_EVERY", "SAMPLE_STEPS", "VALIDATE_EVERY", "VALIDATION"] },
    ],
  },
  "CHECKPOINT / EXPORT": {
    title: "CHECKPOINT / EXPORT",
    note: "SAVE_FORMAT controls export files; checkpoints follow SAVE_EVERY / SAVE_MINUTES.",
    subgroups: [
      { title: "SAVE", names: ["SAVE_EVERY", "SAVE_MINUTES", "KEEP_LAST", "FINAL_CHECKPOINT", "RESUME"] },
      { title: "EXPORT", names: ["SAVE_FORMAT", "SAVE_MERGED", "SAVE_FUSED_QKV", "CHECKPOINT_EXPORT", "EXPORT_FORMATS"] },
    ],
  },
};

const PAGE_TITLES = {
  OPTIM: "OPTIMIZE",
};

/* Trainer section titles do not match the dictionary slugs one to one: five of
   them carry a second concept in the page name (MODEL / WEIGHTS and friends). */
const SECTION_SLUGS = {
  MODEL: "model_weights",
  DATA: "data_cache",
  ADAPTER: "adapter_lora",
  MEMORY: "memory_plan",
  RUNTIME: "runtime_loop",
};

const RELATIONS = [
  {
    names: MEMORY_MANUAL,
    kind: "manual",
    noteKey: "knob.rel.manual",
    test: (get) => get("MEMORY_PROFILE") === "manual",
  },
  {
    names: ["BATCH_SIZE"],
    kind: "unused",
    noteKey: "knob.rel.batchCount",
    test: (get) => get("BATCHING") === "count",
  },
  {
    names: ["MAX_SAMPLES"],
    kind: "requires",
    noteKey: "knob.rel.maxSamples",
    test: (get) => get("PARALLEL") !== "pipeline" || Number(get("MAX_SAMPLES") || 0) > 0,
  },
  {
    names: ["TOKEN_BUDGET"],
    kind: "conflict",
    noteKey: "knob.rel.tokenBudget",
    test: (get) => get("PARALLEL") !== "pipeline" || get("TOKEN_BUDGET") === "exact",
  },
  {
    names: ["RESOLUTION_MODE", "CROP"],
    kind: "conflict",
    noteKey: "knob.rel.cacheCrop",
    test: (get) => get("LATENT_CACHE") !== "offline"
      || (get("RESOLUTION_MODE") !== "shifted_square" && get("CROP") === "center"),
  },
  {
    names: ["TEXT_DEVICE"],
    kind: "conflict",
    onlyIfSet: true,
    noteKey: "knob.rel.textDevice",
    test: (get) => get("TEXT_DEVICE") !== "cpu" || get("TEXT_CACHE") === "offline",
  },
  {
    names: ["FP16_FIX", "FP16_FIX_KWARGS"],
    kind: "unused",
    skipValues: ["", "none", "None"],
    noteKey: "knob.rel.fp16Fix",
    test: (get) => ["fp16", "auto"].includes(get("PRECISION")),
  },
  {
    names: ["SAMPLE_NEGATIVE", "SAMPLE_CFG", "SAMPLE_SHIFT", "SAMPLE_SKIP_LAYERS", "SAMPLE_SIZE", "SAMPLE_SEEDS"],
    kind: "unused",
    noteKey: "knob.rel.sampling",
    test: (get) => Number(get("SAMPLE_EVERY") || 0) > 0,
  },
  {
    names: ["SAVE_MERGED", "SAVE_FUSED_QKV"],
    kind: "unused",
    noteKey: "knob.rel.export",
    test: (get) => get("SAVE_FORMAT") !== "none",
  },
  {
    names: ["PRESET", "DIM", "ALPHA", "DORA", "DORA_DETACH", "DROPOUT", "LORAPLUS_RATIO"],
    kind: "unused",
    noteKey: "knob.rel.adapterFull",
    test: (get) => get("ALGO") !== "full",
  },
  {
    names: ["GRAD_OFFLOAD"],
    kind: "unused",
    noteKey: "knob.rel.gradOffload",
    test: (get) => Number(get("GRAD_ACC") || 1) > 1,
  },
];

function humanize(name) {
  return name.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function tierFor(name, section) {
  if (BASE_NAMES.has(name)) return "base";
  if (EXPERT_NAMES.has(name) || EXPERT_SECTIONS.has(section)) return "expert";
  return "advanced";
}

function knobValue(knob, values) {
  const value = Object.prototype.hasOwnProperty.call(values, knob.name) ? values[knob.name] : knob.value;
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof knob.value === "boolean" && ["true", "false"].includes(String(value).toLowerCase())) {
    return String(value).toLowerCase() === "true" ? "True" : "False";
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function initialValues(sections, values) {
  const initial = {};
  sections.forEach((section) => {
    section.knobs.forEach((knob) => { initial[knob.name] = knobValue(knob, values); });
  });
  return initial;
}

function fieldHtml(knob, current, env) {
  const name = knob.name;
  const value = String(current ?? "");
  if (typeof knob.value === "boolean" && !TEXT_BOOLEANS.has(name)) {
    return `<select class="field" data-knob="${esc(name)}"><button type="button"><selectedcontent></selectedcontent></button><option value="True" ${value === "True" ? "selected" : ""}>true</option><option value="False" ${value !== "True" ? "selected" : ""}>false</option></select>`;
  }
  const options = ENUMS[name];
  if (options) {
    const known = options.includes(value);
    const placeholder = value && !known
      ? `<option value="${esc(value)}" selected>${esc(value)}</option>`
      : `<option value="" ${value ? "" : "selected"}>${esc(env.t("knob.state.default"))}</option>`;
    return `<select class="field" data-knob="${esc(name)}"><button type="button"><selectedcontent></selectedcontent></button>${placeholder}${options.map((option) => `<option value="${esc(option)}" ${option === value ? "selected" : ""}>${esc(option)}</option>`).join("")}</select>`;
  }
  const isJson = (knob.value !== null && typeof knob.value === "object") || value.startsWith("{") || value.startsWith("[");
  if (isJson) {
    return `<input class="field" data-knob="${esc(name)}" data-kind="json" value="${esc(value)}" spellcheck="false">`;
  }
  const placeholder = !value && knob.default && knob.default.length <= 40
    ? ` placeholder="${esc(knob.default)}"`
    : "";
  return `<input class="field" data-knob="${esc(name)}" value="${esc(value)}"${placeholder} spellcheck="false">`;
}

function relationFor(name, value, read) {
  for (const relation of RELATIONS) {
    if (!relation.names.includes(name)) continue;
    if (relation.test(read)) continue;
    if (relation.kind === "manual") {
      return { state: value ? "conflict" : "requires", note: relation.noteKey };
    }
    if (relation.kind === "conflict") {
      if (relation.onlyIfSet && !value) return null;
      return { state: value ? "conflict" : "requires", note: relation.noteKey };
    }
    if (relation.kind === "requires") {
      return { state: "requires", note: relation.noteKey };
    }
    if (relation.kind === "unused") {
      if (relation.skipValues && relation.skipValues.includes(value)) return null;
      return { state: "unused", note: relation.noteKey };
    }
  }
  return null;
}

function rowState(knob, value, initial, read) {
  const relation = relationFor(knob.name, value, read);
  const changed = String(value) !== String(initial ?? "");
  if (relation?.state === "conflict") {
    return { state: "CONFLICT", note: relation.text, bad: true };
  }
  if (changed) {
    return { state: "CHANGED", note: relation?.text || "", bad: false };
  }
  if (relation?.state === "requires") {
    return { state: "REQUIRES", note: relation.text, bad: false };
  }
  if (relation?.state === "unused") {
    return { state: "UNUSED", note: relation.text, bad: false };
  }
  if (knob.value === null && value === "" && knob.default && !["None", ""].includes(knob.default)) {
    return { state: "EXPR", note: "knob.note.expr", bad: false };
  }
  if (value === "" && (knob.value === null || knob.value === undefined)) {
    return { state: "UNSET", note: "knob.note.unset", bad: false };
  }
  return { state: "DEFAULT", note: "", bad: false };
}

function renderRow(knob, group, subgroup, source, initial, scriptName, env) {
  const t = env.t;
  const label = LABELS[knob.name] || humanize(knob.name);
  const helpId = `knobhelp-${knob.name}`;
  const current = initial[knob.name];
  const comment = esc(knob.comment || t("knob.help.noComment"));
  const typeText = knob.type
    || (knob.value === null || knob.value === undefined
      ? "inferred"
      : (Array.isArray(knob.value) ? "list" : (typeof knob.value === "object" ? "dict" : typeof knob.value)));
  const haystack = [knob.name, label, group.source, group.rawTitle, subgroup.raw, knob.comment || ""].join(" ").toLowerCase();
  const entry = env.docMap ? env.docMap[knob.name] : null;
  const docsText = entry
    ? String((env.lang === "zh" ? entry.zh : entry.en) || entry.en || entry.zh || "")
    : "";
  const docsPath = entry && entry.doc ? String(entry.doc) : "";
  const docsHtml = docsText
    ? `<p class="knob-help-desc">${esc(docsText)}</p>`
    : `<p class="knob-help-desc dim">${esc(t("knob.help.noDocs"))}</p>`;
  const docsPathHtml = docsPath
    ? `<dt>${esc(t("knob.help.docs"))}</dt><dd class="knob-help-doc dim" title="${esc(docsPath)}">${esc(docsPath)}</dd>`
    : "";
  return `<div class="knobrow" data-knob-row data-name="${esc(knob.name)}" data-tier="${tierFor(knob.name, source)}" data-search="${esc(haystack)}">
    <div class="lab"><span class="rawname">${esc(knob.name)}</span><span class="friendly">${esc(label)}</span></div>
    <div class="val">
      <div class="knobline">
        <div class="knobcontrol">${fieldHtml(knob, current, env)}</div>
        <div class="knobmeta">
          <span class="knob-state" data-state="default">${esc(t("knob.state.default"))}</span>
          <button type="button" class="knob-help-btn" aria-expanded="false" aria-controls="${helpId}" aria-label="${esc(t("knob.help", { name: knob.name }))}">?</button>
          <button type="button" class="knob-reset" hidden>${esc(t("knob.reset"))}</button>
        </div>
      </div>
      <div class="knob-note" hidden></div>
      <div class="knob-help" id="${helpId}" hidden>
        ${docsHtml}
        <p>${comment}</p>
        <dl>
          <dt>${esc(t("knob.help.default"))}</dt><dd>${esc(knob.default || (knob.value === null ? "None" : String(knob.value)))}</dd>
          <dt>${esc(t("knob.help.source"))}</dt><dd>${esc(scriptName)}:${knob.line}</dd>
          <dt>${esc(t("knob.help.type"))}</dt><dd>${esc(typeText)}</dd>
          <dt>${esc(t("knob.help.state"))}</dt><dd class="knob-help-state">${esc(t("knob.state.default"))}</dd>
          <dt>${esc(t("knob.help.relation"))}</dt><dd class="knob-help-relation">${esc(t("knob.help.noRelation"))}</dd>
          ${docsPathHtml}
        </dl>
      </div>
    </div>
  </div>`;
}

/* Stable dictionary suffix for a section/subgroup title: "WEIGHTS & PATHS"
   becomes "weights_paths". An unknown upstream section keeps its English
   title through tr(). */
function slug(text) {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function buildPages(sections, env) {
  const byName = new Map();
  sections.forEach((section) => section.knobs.forEach((knob) => byName.set(knob.name, knob)));
  return sections.map((section, index) => {
    const meta = GROUP_META[section.title] || { subgroups: [] };
    const sectionNames = new Set(section.knobs.map((knob) => knob.name));
    const assigned = new Set();
    const subgroups = [];
    meta.subgroups.forEach((subgroup) => {
      const knobs = subgroup.names
        .map((name) => byName.get(name))
        .filter((knob) => knob && sectionNames.has(knob.name) && !assigned.has(knob.name));
      if (!knobs.length) return;
      knobs.forEach((knob) => assigned.add(knob.name));
      subgroups.push({ ...subgroup, source: section.title, knobs });
    });
    const rest = section.knobs.filter((knob) => knob.name !== "RUN_NAME" && !assigned.has(knob.name));
    if (rest.length) {
      subgroups.push({
        title: meta.subgroups.length ? env.t("knob.group.other") : "",
        note: "",
        source: section.title,
        knobs: rest,
      });
    }
    const rows = subgroups.flatMap((subgroup) => subgroup.knobs.map((knob) => ({
      knob, subgroup: subgroup.title, tier: tierFor(knob.name, section.title),
    })));
    const englishTitle = PAGE_TITLES[section.title] || section.title;
    const pageKey = SECTION_SLUGS[section.title] || slug(section.title);
    return {
      key: `p${index}`,
      title: env.tr(`knob.page.${pageKey}`, englishTitle),
      rawTitle: englishTitle,
      source: section.title,
      note: meta.note ? env.tr(`knob.page.${pageKey}.note`, meta.note) : "",
      subgroups: subgroups.map((subgroup) => ({
        ...subgroup,
        raw: subgroup.title,
        title: subgroup.title ? env.tr(`knob.sub.${slug(subgroup.title)}`, subgroup.title) : "",
        note: subgroup.note ? env.tr(`knob.sub.${slug(subgroup.title)}.note`, subgroup.note) : "",
      })),
      rows,
    };
  }).filter((page) => page.rows.length);
}

function renderPage(group, initial, scriptName, env) {
  const t = env.t;
  const body = group.subgroups.map((subgroup) => `<div class="kgsubgroup">
    ${subgroup.title ? `<div class="kgsubhead">${esc(subgroup.title)}${subgroup.note ? ` <span class="kgnote">${esc(subgroup.note)}</span>` : ""}</div>` : ""}
    <div class="form">
      ${subgroup.knobs.map((knob) => renderRow(knob, group, subgroup, subgroup.source, initial, scriptName, env)).join("")}
    </div>
  </div>`).join("");
  return `<section class="knobpage" id="panel-${group.key}" role="tabpanel" aria-labelledby="tab-${group.key}" data-group="${group.key}" hidden>
    <div class="knobpage-head">
      <span class="kgsource">${esc(t("knob.source", { section: group.rawTitle }))}</span>
      <span class="kgcount">${esc(t("knob.count", { visible: 0, total: group.rows.length }))}</span>
      ${group.note ? `<span class="kgnote">${esc(group.note)}</span>` : ""}
    </div>
    <div class="knobpage-empty" hidden></div>
    <div class="kgbody">${body}</div>
  </section>`;
}

function renderWorkspace(groups, initial, scriptName, env) {
  const t = env.t;
  const rows = groups.flatMap((group) => group.rows);
  const total = rows.length;
  const base = rows.filter((row) => row.tier === "base").length;
  const advanced = rows.filter((row) => row.tier !== "expert").length;
  const tabs = groups.map((group, index) => `<button type="button" class="${index === 0 ? "on" : ""}" id="tab-${group.key}" role="tab" aria-selected="${index === 0}" aria-controls="panel-${group.key}" tabindex="${index === 0 ? 0 : -1}" data-group="${group.key}">${esc(group.title)}</button>`).join("");
  return `<div class="knobswork surface" data-level="basic">
    <div class="knobbar">
      <div class="knobsearch">
        <label for="knobsearch">${esc(t("knob.search"))}</label>
        <input class="field" id="knobsearch" type="search" placeholder="${esc(t("knob.search.ph"))}" autocomplete="off" spellcheck="false">
      </div>
      <div class="knoblevels" role="group" aria-label="${esc(t("knob.level.aria"))}">
        <button type="button" class="btn on" data-level="basic" aria-pressed="true">${esc(t("knob.level.basic"))} <span>${base}</span></button>
        <button type="button" class="btn" data-level="advanced" aria-pressed="false">${esc(t("knob.level.advanced"))} <span>${advanced}</span></button>
        <button type="button" class="btn" data-level="all" aria-pressed="false">${esc(t("knob.level.all"))} <span>${total}</span></button>
      </div>
    </div>
    <div class="knobsummary" id="knobsummary">${esc(t("knob.summary", { total, base, advanced }))}</div>
    <div class="knobtabs-wrap">
      <div class="tabs knobtabs" role="tablist" aria-label="${esc(t("knob.pages.aria"))}">${tabs}</div>
    </div>
    <div class="knobempty" id="knobempty" hidden></div>
    <div class="knobpages">${groups.map((group) => renderPage(group, initial, scriptName, env)).join("")}</div>
  </div>`;
}

/**
 * Mount the knob workspace.
 * @param {HTMLElement} root container
 * @param {{sections: Array, initial: object, script?: string, onChange?: Function,
 *          t?: Function, docs?: object}} [options]
 *   t    - dictionary lookup (ctx.t). Missing: identity, so an old caller keeps
 *          working instead of throwing; titles then fall back to English.
 *   docs - knob-docs payload ({knobs:{name:{en,zh,doc}}}) or the bare knob map;
 *          null/undefined means "no explanation written yet".
 */
export function mountKnobForm(root, { sections, initial, script, onChange, t, docs } = {}) {
  const translate = typeof t === "function" ? t : (key) => key;
  /* A dictionary miss must not print a raw key: tr keeps the English literal. */
  const tr = (key, fallback) => {
    const text = translate(key);
    return text === key ? fallback : text;
  };
  const docMap = docs && typeof docs === "object"
    ? (docs.knobs && typeof docs.knobs === "object" ? docs.knobs : docs)
    : null;
  const env = {
    t: translate,
    tr,
    docMap,
    lang: document.documentElement.dataset.lang === "zh" ? "zh" : "en",
  };
  const groups = buildPages(sections, env);
  const scriptName = (script || "moss.py").split(/[\\/]/).pop();
  root.innerHTML = renderWorkspace(groups, initial, scriptName, env);

  const work = root.querySelector(".knobswork");
  const rows = [...work.querySelectorAll("[data-knob-row]")];
  const panels = [...work.querySelectorAll(".knobpage")];
  const tabs = [...work.querySelectorAll(".knobtabs button")];
  const levelButtons = [...work.querySelectorAll("[data-level]")];
  const search = work.querySelector("#knobsearch");
  const summary = work.querySelector("#knobsummary");
  const empty = work.querySelector("#knobempty");
  const panelByGroup = new Map(panels.map((panel) => [panel.dataset.group, panel]));
  const tabByGroup = new Map(tabs.map((tab) => [tab.dataset.group, tab]));
  const baseCount = rows.filter((row) => row.dataset.tier === "base").length;
  const knobByName = new Map();
  sections.forEach((section) => section.knobs.forEach((knob) => knobByName.set(knob.name, knob)));
  let activeGroup = groups[0]?.key || "";
  let beforeSearch = "";
  let wasSearching = false;

  function readAll() {
    const values = {};
    work.querySelectorAll("[data-knob]").forEach((input) => { values[input.dataset.knob] = input.value; });
    return values;
  }

  function updateStates() {
    const values = readAll();
    let changed = 0;
    let conflicts = 0;
    rows.forEach((row) => {
      const name = row.dataset.name;
      const input = row.querySelector("[data-knob]");
      const knob = knobByName.get(name);
      if (!input || !knob) return;
      const result = rowState(knob, input.value, initial[name], (other) => values[other] ?? "");
      const stateEl = row.querySelector(".knob-state");
      stateEl.textContent = env.t(`knob.state.${result.state.toLowerCase()}`);
      stateEl.dataset.state = result.state.toLowerCase();
      const noteEl = row.querySelector(".knob-note");
      noteEl.textContent = result.note ? env.t(result.note) : "";
      noteEl.hidden = !result.note;
      noteEl.classList.toggle("bad", result.bad);
      const reset = row.querySelector(".knob-reset");
      reset.hidden = String(input.value) === String(initial[name] ?? "");
      row.querySelector(".knob-help-state").textContent = env.t(`knob.state.${result.state.toLowerCase()}`);
      row.querySelector(".knob-help-relation").textContent = result.note ? env.t(result.note) : env.t("knob.help.noRelation");
      if (String(input.value) !== String(initial[name] ?? "")) changed += 1;
      if (result.state === "CONFLICT") conflicts += 1;
    });
    return { changed, conflicts };
  }

  function applyVisibility() {
    const level = work.dataset.level;
    const query = search.value.trim().toLowerCase();
    const terms = query.split(/\s+/).filter(Boolean);
    const searching = Boolean(query);
    const was = wasSearching;
    if (searching && !was) beforeSearch = activeGroup;
    wasSearching = searching;

    let visibleRows = 0;
    rows.forEach((row) => {
      const tier = row.dataset.tier;
      const levelOk = level === "all"
        || (level === "advanced" && tier !== "expert")
        || (level === "basic" && tier === "base");
      const haystack = row.dataset.search || "";
      const searchOk = !terms.length || terms.every((term) => haystack.includes(term));
      const show = searching ? searchOk : levelOk;
      row.hidden = !show;
      if (show) visibleRows += 1;
    });

    const visibleGroups = [];
    groups.forEach((group) => {
      const panel = panelByGroup.get(group.key);
      const tab = tabByGroup.get(group.key);
      const groupRows = [...panel.querySelectorAll("[data-knob-row]")];
      const visible = groupRows.filter((row) => !row.hidden).length;
      tab.hidden = searching && visible === 0;
      panel.querySelector(".kgcount").textContent = env.t("knob.count", { visible, total: groupRows.length });
      const pageEmpty = panel.querySelector(".knobpage-empty");
      if (searching) {
        pageEmpty.hidden = true;
      } else {
        pageEmpty.hidden = visible > 0;
        pageEmpty.textContent = visible
          ? ""
          : env.t("knob.empty.level", { level: env.t(`knob.level.${level}`) });
      }
      panel.querySelectorAll(".kgsubgroup").forEach((subgroup) => {
        const subgroupRows = [...subgroup.querySelectorAll("[data-knob-row]")];
        subgroup.hidden = subgroupRows.every((row) => row.hidden);
      });
      if (visible) visibleGroups.push(group.key);
    });

    if (!searching && was) activeGroup = beforeSearch || activeGroup;
    if (searching) {
      if (!visibleGroups.includes(activeGroup)) activeGroup = visibleGroups[0] || "";
    } else if (!panelByGroup.has(activeGroup)) {
      activeGroup = groups[0]?.key || "";
    }
    panels.forEach((panel) => { panel.hidden = panel.dataset.group !== activeGroup; });
    tabs.forEach((tab) => {
      const on = tab.dataset.group === activeGroup;
      tab.classList.toggle("on", on);
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    });
    const currentTab = tabByGroup.get(activeGroup);
    const strip = work.querySelector(".knobtabs-wrap");
    if (currentTab && strip) {
      const tabRect = currentTab.getBoundingClientRect();
      const stripRect = strip.getBoundingClientRect();
      if (tabRect.left < stripRect.left || tabRect.right > stripRect.right) {
        currentTab.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }
    empty.hidden = !searching || visibleGroups.length > 0;
    empty.textContent = searching && visibleGroups.length === 0 ? env.t("knob.empty.search", { query }) : "";

    const counts = updateStates();
    const bits = [env.t("knob.summary", {
      total: rows.length,
      base: baseCount,
      advanced: rows.filter((row) => row.dataset.tier !== "expert").length,
    })];
    if (counts.changed) bits.push(env.t("knob.summary.changed", { n: counts.changed }));
    if (counts.conflicts) bits.push(env.t("knob.summary.conflict", { n: counts.conflicts }));
    summary.textContent = searching
      ? env.t("knob.summary.search", { n: visibleRows, total: rows.length })
      : bits.join(" · ");
  }

  function setLevel(next) {
    work.dataset.level = next;
    levelButtons.forEach((button) => {
      const on = button.dataset.level === next;
      button.classList.toggle("on", on);
      button.setAttribute("aria-pressed", String(on));
    });
  }

  function activateGroup(key, focus = false) {
    if (!key) return;
    activeGroup = key;
    applyVisibility();
    if (focus) tabByGroup.get(key)?.focus();
  }

  rows.forEach((row) => {
    const help = row.querySelector(".knob-help-btn");
    const panel = row.querySelector(".knob-help");
    help.addEventListener("click", () => {
      const open = help.getAttribute("aria-expanded") === "true";
      help.setAttribute("aria-expanded", String(!open));
      help.classList.toggle("on", !open);
      panel.hidden = open;
    });
    row.querySelector(".knob-reset").addEventListener("click", () => {
      const input = row.querySelector("[data-knob]");
      input.value = initial[row.dataset.name] ?? "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
    });
  });

  levelButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (search.value) search.value = "";
      setLevel(button.dataset.level);
      applyVisibility();
    });
  });

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activateGroup(tab.dataset.group));
    tab.addEventListener("keydown", (event) => {
      const visibleTabs = tabs.filter((item) => !item.hidden);
      const index = visibleTabs.indexOf(tab);
      if (index < 0) return;
      let next = null;
      if (event.key === "ArrowRight") next = visibleTabs[(index + 1) % visibleTabs.length];
      else if (event.key === "ArrowLeft") next = visibleTabs[(index - 1 + visibleTabs.length) % visibleTabs.length];
      else if (event.key === "Home") next = visibleTabs[0];
      else if (event.key === "End") next = visibleTabs[visibleTabs.length - 1];
      if (!next) return;
      event.preventDefault();
      activateGroup(next.dataset.group, true);
    });
  });

  search.addEventListener("input", applyVisibility);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      search.value = "";
      applyVisibility();
      search.blur();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const activePanel = panelByGroup.get(activeGroup);
      const first = activePanel
        ? [...activePanel.querySelectorAll("[data-knob-row]")].find((row) => !row.hidden)
        : null;
      if (first) {
        first.scrollIntoView({ block: "center" });
        first.querySelector("[data-knob]").focus();
      }
    }
  });

  work.addEventListener("input", (event) => {
    if (event.target.matches("[data-knob]")) {
      applyVisibility();
      onChange?.();
    }
  });
  work.addEventListener("change", (event) => {
    if (event.target.matches("[data-knob]")) {
      applyVisibility();
      onChange?.();
    }
  });

  applyVisibility();
}

function pyLiteral(value, kind) {
  if (kind !== "json") return value;
  try {
    const parsed = JSON.parse(value);
    const convert = (item) => {
      if (item === null) return "None";
      if (item === true) return "True";
      if (item === false) return "False";
      if (Array.isArray(item)) return `[${item.map(convert).join(", ")}]`;
      if (typeof item === "object") return `{${Object.entries(item).map(([key, val]) => `${JSON.stringify(key)}: ${convert(val)}`).join(", ")}}`;
      if (typeof item === "string") return JSON.stringify(item);
      return String(item);
    };
    return convert(parsed);
  } catch {
    return value;
  }
}

export function collectOverrides(root, initial) {
  const overrides = {};
  root.querySelectorAll("[data-knob]").forEach((input) => {
    const name = input.dataset.knob;
    if (name === "RUN_NAME") return;
    if (initial[name] !== input.value) overrides[name] = pyLiteral(input.value, input.dataset.kind);
  });
  return overrides;
}
