// H3MD - 'long video' labels + merge_images clean-up (helpers in h3md_long_video.js).
import { app } from "../../scripts/app.js";
import { LOOP_NODES, SAVE_NODE, SAVE_REMOVED_WIDGETS, relabelLoopOutput, relabelSaveInput,
         staleInputSlots, fixSaveOutputs } from "./h3md_long_video.js";

function dropFps(node, info = null) {
  const dropped = fixSaveOutputs(node, info);
  if (dropped.length) console.warn(`[H3MD] H3MDSaveVideoInterp #${node.id}: the 'fps' output was removed; ` +
                                   `${dropped.length} link(s) from it were dropped`);
}

function finishOldFps(node) {
  if (!node?.__h3mdOldFps) return;
  const old = node.__h3mdOldFps === "old";
  delete node.__h3mdOldFps;
  try {
    const o = node.outputs?.[2];
    if (o && old) { o.name = "fps"; o.type = "FLOAT"; }   // saved as fps: fixSaveOutputs drops all its links
    dropFps(node);                                         // else only non-VIDEO links (slot renamed by the def)
    node.setDirtyCanvas?.(true, true);
  } catch (e) { console.error("[H3MD] dropping old fps links failed", e); }
}

app.registerExtension({
  name: "H3MD.LongVideo",
  afterConfigureGraph() {
    for (const node of app.graph?._nodes ?? app.graph?.nodes ?? []) finishOldFps(node);
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const loop = LOOP_NODES.includes(nodeData?.name), save = nodeData?.name === SAVE_NODE;
    if (!loop && !save) return;
    const apply = (node, info = null) => {
      try {
        if (loop) {
          relabelLoopOutput(node);
          for (const k of staleInputSlots(node)) node.removeInput?.(k);
        } else {
          for (const k of staleInputSlots(node, SAVE_REMOVED_WIDGETS)) node.removeInput?.(k);
          relabelSaveInput(node);
          // while a graph loads the link targets may not exist yet: drop the old fps links after the whole
          // graph is configured (afterConfigureGraph) - here only rename the slot and remember it
          if (info) {
            node.__h3mdOldFps = fixSaveOutputs.isOld(node, info) ? "old" : "check";
            setTimeout(() => finishOldFps(node), 0);   // fallback when afterConfigureGraph is not fired (paste)
          } else dropFps(node);
        }
        node.setDirtyCanvas?.(true, true);
      } catch (e) { console.error("[H3MD] long video labels failed", e); }
    };
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      apply(this);
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure?.apply(this, arguments);
      apply(this, info);
      return r;
    };
  },
});
