const fs = require('fs');
const path = require('path');
const Module = require('module');
const r = Module.createRequire('/app/apps/web/package.json');
process.chdir('/home/liewzheng/Workspace/github.com/plane/apps/web');
const React = r('react');
const { Document, Font, Page, StyleSheet, pdf } = r('@react-pdf/renderer');
const { Html } = r('react-pdf-html');
const { applyPdfTableLayoutToHtml } = r('@plane/utils');
const F = (p) => '/app/apps/web/app/assets/fonts/' + p;
Font.register({family: 'Open Sans', fonts: [
  { src: F('open-sans/open-sans-regular.ttf'), fontWeight: 'normal' },
  { src: F('open-sans/open-sans-italic.ttf'), fontWeight: 'normal', fontStyle: 'italic' },
  { src: F('open-sans/open-sans-700.ttf'), fontWeight: 'bold' },
  { src: F('open-sans/open-sans-700italic.ttf'), fontWeight: 'bold', fontStyle: 'italic' },
]});
Font.register({family: 'Inter', fonts: [
  { src: F('inter/regular.ttf'), fontWeight: 'normal' },
  { src: F('inter/bold.ttf'), fontWeight: 'bold' },
]});
const remToPx = (rem) => rem * 0.9 * 16;
const EDITOR_PDF_DOCUMENT_STYLESHEET = StyleSheet.create({
  "h1.page-title": { fontSize: remToPx(1.8), fontWeight: "bold", lineHeight: 1.2, marginTop: 0, marginBottom: remToPx(1), paddingBottom: remToPx(0.3), borderBottom: "1px solid #eee" },
  "h1:not(.page-title)": { fontSize: remToPx(1.8), fontWeight: "bold", lineHeight: 1.2, marginTop: remToPx(1), marginBottom: remToPx(1), paddingBottom: remToPx(0.3), borderBottom: "1px solid #eee" },
  "h2": { fontSize: remToPx(1.4), fontWeight: "bold", lineHeight: 1.225, marginTop: remToPx(1), marginBottom: remToPx(1), paddingBottom: remToPx(0.3), borderBottom: "1px solid #eee" },
  "h3": { fontSize: remToPx(1.2), fontWeight: "bold", lineHeight: 1.43, marginTop: remToPx(1), marginBottom: remToPx(1) },
  "h4": { fontSize: remToPx(1), fontWeight: "bold", marginTop: remToPx(1), marginBottom: remToPx(1) },
  "h5": { fontSize: remToPx(0.8), fontWeight: "bold", marginTop: remToPx(1), marginBottom: remToPx(1) },
  "h6": { fontSize: remToPx(0.8), fontWeight: "bold", color: "#777777", marginTop: remToPx(1), marginBottom: remToPx(1) },
  "p:not(table p)": { fontSize: remToPx(0.875), lineHeight: 1.5 },
  "p:not(ol p, ul p)": { marginTop: remToPx(0.5), marginBottom: remToPx(0.5) },
  "table": { marginTop: remToPx(0.8), marginBottom: remToPx(0.8), marginHorizontal: 0, width: "100%" },
  "table thead, table tbody": { width: "100%" },
  "table tr": { width: "100%" },
  "table td": { paddingVertical: 6, paddingHorizontal: 13, border: "1px solid #dfe2e5", fontSize: remToPx(0.875), lineHeight: 1.5, width: "100%" },
  "table th": { paddingVertical: 6, paddingHorizontal: 13, border: "1px solid #dfe2e5", backgroundColor: "#f8f8f8", fontWeight: "bold", fontSize: remToPx(0.875), lineHeight: 1.5, width: "100%" },
  "table p": { fontSize: remToPx(0.875), lineHeight: 1.5 },
});
const stylesheet = {
  ...EDITOR_PDF_DOCUMENT_STYLESHEET,
  "*:not(.courier, .courier-bold)": { fontFamily: ["Open Sans","Inter","Noto Sans SC"] },
};
(async () => {
  const rawHtml = fs.readFileSync('/tmp/pdbg/s82p.html','utf8');
  const contentWidthPx = 595.28 - 128;
  const htmlWithWidths = applyPdfTableLayoutToHtml(rawHtml, contentWidthPx);
  const e = React.createElement(Document, null,
    React.createElement(Page, { size: 'A4', style: { backgroundColor: '#ffffff', padding: 64 } },
      React.createElement(Html, { stylesheet }, htmlWithWidths)));
  const blob = await pdf(e).toBlob();
  fs.writeFileSync('/tmp/pdbg/s82-new.pdf', Buffer.from(await blob.arrayBuffer()));
  console.log('written', fs.statSync('/tmp/pdbg/s82-new.pdf').size);
})().catch(e => { console.error(e); process.exit(1); });
