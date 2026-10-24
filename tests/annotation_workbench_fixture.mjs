/* Synthetic beta group for browser-only acceptance. Imported by the HTTP fixture
 * (tests/annotation_browser.mjs) and never by the Node DOM tests, so the 64-image
 * alpha fixture stays exactly as the existing suites expect it. Pure memory. */
import { docs, candidates, id, hash } from "./annotation_fixture.mjs";

const rawText = "cat, cat, dog";
const documentId = id("doc", 65), bindingId = id("bind", 65), recordId = id("rec", 65);
const betaDoc = {
  libraryId: id("lib", 1), documentId, group: "beta", path: "beta-unconfirmed.txt", format: "txt",
  sourceHash: hash(65), sourceVersion: id("ver", 65), generation: 1, bytes: Buffer.byteLength(rawText),
  bom: false, newline: "none", finalNewline: false, rawText,
  // No interpretation has been confirmed yet: the document is a bounded raw preview.
  bindings: [], fields: [], readOnly: true, readOnlyReason: "SCHEMA_REQUIRED",
};
docs.set(documentId, betaDoc);

const candidate = { documentId, path: betaDoc.path, format: "txt", sourceHash: betaDoc.sourceHash,
  sourceVersion: betaDoc.sourceVersion, bytes: betaDoc.bytes, bindingIds: [], suggestedKind: null, status: "unconfirmed" };

const betaImages = [
  { itemId: id("item", 65), group: "beta", imagePath: "beta-unconfirmed.png", imageUrl: "/files/beta/beta-unconfirmed.png",
    candidates: [candidate], bindingId, annotationStatus: "unconfirmed" },
  { itemId: id("item", 66), group: "beta", imagePath: "beta-missing.png", imageUrl: "/files/beta/beta-missing.png",
    candidates: [], bindingId: null, annotationStatus: "missing" },
];
candidates.set(betaImages[0].itemId, [candidate]);
candidates.set(betaImages[1].itemId, []);

export { betaImages, betaDoc };
