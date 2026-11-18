#!/usr/bin/env node
// Plan-level Remake Swap field tests (no Comfy, no GUI).
import { pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const planUrl = pathToFileURL(join(here, "..", "web", "js", "h3md_plan.js")).href;
const M = await import(planUrl);

let failed = 0;
let passed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log("ok  " + name); }
  else { failed++; console.log("FAIL " + name + (detail ? " :: " + detail : "")); }
}

const p0 = M.newPlan();
check("newPlan has no swapEngine", !("swapEngine" in p0));
check("newPlan remake is off", M.remakeOn(p0) === false);
check("newPlan segments have no keyframes", p0.segments.every((s) => !("keyframes" in s)));

const oldText = M.serializePlan(p0);
const oldParsed = M.parsePlanText(oldText);
check("parsePlanText succeeds", !oldParsed.error && oldParsed.plan);
const oldBack = oldParsed.plan;
check("old plan serialize has no swapEngine", !("swapEngine" in oldBack) && !oldText.includes("swapEngine"));
check("old plan stays off after parse", M.remakeOn(oldBack) === false);

M.setRemake(p0, true);
check("setRemake(true) writes remake", p0.swapEngine === "remake" && M.remakeOn(p0) === true);

const pWin = M.parsePlanText(JSON.stringify({
  version: 1,
  media: { images: [], videos: [], audios: [], refmods: [] },
  windowMode: "long",
  segments: [{ length: 396, prompt: "" }],
})).plan;
check("windowMode can exist on old plans", pWin.windowMode === "long");
M.setRemake(pWin, true);
check("turning remake on drops windowMode", pWin.swapEngine === "remake" && !("windowMode" in pWin));

const img = M.addMedia(p0, "images", { name: "face.png" }).id;
check("addMedia returns an images id", typeof img === "string" && img.length > 0, String(img));
const added = M.addShotKeyframe(p0, 0, img, 1.5);
check("add keyframe with library id", added.ok === true, JSON.stringify(added));
check("keyframes bound on segment", Array.isArray(p0.segments[0].keyframes) && p0.segments[0].keyframes[0].image === img && p0.segments[0].keyframes[0].at === 1.5);

const badId = M.addShotKeyframe(p0, 0, "i_does_not_exist", 2);
check("unknown media id is rejected", badId.ok === false && /not in media/.test(badId.notes.join(" ")));
check("rejected add does not invent an id", p0.segments[0].keyframes.every((k) => k.image === img));

const badAt = M.addShotKeyframe(p0, 0, img, 99);
check("at outside shot is rejected", badAt.ok === false && /outside/.test(badAt.notes.join(" ")));

const ov = M.addShotKeyframe(p0, 0, img, 0);
check("at 0 is valid when overlap is 0", ov.ok === true, JSON.stringify(ov));

p0.segments[0].overlapIn = 24;
const inOv = M.addShotKeyframe(p0, 0, img, 0.5);
check("at inside overlap is rejected", inOv.ok === false && /overlap/.test(inOv.notes.join(" ")));
p0.segments[0].overlapIn = 0;

const pMax = M.newPlan();
M.setRemake(pMax, true);
const a = M.addMedia(pMax, "images", { name: "a.png" }).id;
const b = M.addMedia(pMax, "images", { name: "b.png" }).id;
const c = M.addMedia(pMax, "images", { name: "c.png" }).id;
const d = M.addMedia(pMax, "images", { name: "d.png" }).id;
const e = M.addMedia(pMax, "images", { name: "e.png" }).id;
for (const [id, at] of [[a, 1], [b, 2], [c, 3], [d, 4]]) {
  const r = M.addShotKeyframe(pMax, 0, id, at);
  check(`add keyframe at ${at}s`, r.ok === true, JSON.stringify(r));
}
const fifth = M.addShotKeyframe(pMax, 0, e, 5);
check("fifth unique keyframe is rejected", fifth.ok === false && /at most 4/.test(fifth.notes.join(" ")));
check("still four keyframes", pMax.segments[0].keyframes.length === 4);

const replaceSame = M.addShotKeyframe(pMax, 0, e, 4);
check("same-time replace is allowed at the limit", replaceSame.ok === true && pMax.segments[0].keyframes.length === 4);
check("same-time replace uses the new image", pMax.segments[0].keyframes.some((k) => k.image === e && k.at === 4));

const text = M.serializePlan(p0);
check("serialize keeps swapEngine remake", /"swapEngine": "remake"/.test(text));
check("serialize keeps keyframes", /"keyframes"/.test(text) && text.includes(img));
const back = M.parsePlanText(text).plan;
check("parse keeps remake on", M.remakeOn(back) === true && back.swapEngine === "remake");
check("parse keeps keyframes image+at", Array.isArray(back.segments[0].keyframes) && back.segments[0].keyframes.some((k) => k.image === img && typeof k.at === "number"));

p0.segments[0].prompt = "keep me";
p0.segments[0].images = [img];
M.setRemake(p0, false);
check("setRemake(false) deletes swapEngine only", !("swapEngine" in p0) && M.remakeOn(p0) === false);
check("turning remake off keeps keyframes", Array.isArray(p0.segments[0].keyframes) && p0.segments[0].keyframes.length >= 1);
check("turning remake off keeps prompt and images", p0.segments[0].prompt === "keep me" && p0.segments[0].images[0] === img);
const offText = M.serializePlan(p0);
check("off serialize has no swapEngine but keeps keyframes", !offText.includes("swapEngine") && offText.includes("keyframes"));
const offBack = M.parsePlanText(offText).plan;
check("imported off plan stays off", M.remakeOn(offBack) === false);
check("imported off plan keeps keyframes", Array.isArray(offBack.segments[0].keyframes) && offBack.segments[0].keyframes[0].image === img);

M.setRemake(p0, true);
check("mediaUsage includes keyframe image", M.mediaUsage(p0, "images", img).includes(0));
M.renameMedia(p0, "images", img, "hero");
check("renameMedia updates keyframe image id", p0.segments[0].keyframes.every((k) => k.image === "hero") && p0.media.images.some((x) => x.id === "hero"));
check("mediaUsage follows renamed keyframe id", M.mediaUsage(p0, "images", "hero").includes(0));
M.removeMedia(p0, "images", "hero");
check("removeMedia drops that keyframe image", !p0.media.images.some((x) => x && x.id === "hero"));
check("removeMedia removes keyframes using that image", !Array.isArray(p0.segments[0].keyframes) || p0.segments[0].keyframes.every((k) => k.image !== "hero"));

const pOr = M.newPlan();
const oid = M.addMedia(pOr, "images", { name: "gone.png" }).id;
M.addShotKeyframe(pOr, 0, oid, 2);
pOr.media.images = [];
const nOr = M.clearOrphanRefState(pOr);
check("clearOrphanRefState drops missing-image keyframes", !("keyframes" in pOr.segments[0]) && nOr >= 1);

const pDup = M.newPlan();
const did = M.addMedia(pDup, "images", { name: "dup.png" }).id;
M.addShotKeyframe(pDup, 0, did, 3);
M.duplicateSegment(pDup, 0);
check("duplicateSegment copies keyframes", Array.isArray(pDup.segments[1].keyframes) && pDup.segments[1].keyframes[0].image === did && pDup.segments[1].keyframes[0].at === 3);

console.log("");
console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
