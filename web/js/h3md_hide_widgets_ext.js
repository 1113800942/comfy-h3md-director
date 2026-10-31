// H3MD - hides widgets listed in h3md_hide_widgets.js (currently the Loop's delete_cache). Values stay serialised.
import { app } from "../../scripts/app.js";
import { HIDDEN_WIDGETS, hideWidgets } from "./h3md_hide_widgets.js";

app.registerExtension({
  name: "H3MD.HideWidgets",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const names = HIDDEN_WIDGETS[nodeData?.name];
    if (!names) return;
    const apply = (node) => {
      try {
        if (hideWidgets(node, names).length) node.setSize?.([node.size[0], node.computeSize()[1]]);
      } catch (e) { console.error("[H3MD] hide widgets failed", e); }
    };
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      apply(this);
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      apply(this);
      return r;
    };
  },
});
