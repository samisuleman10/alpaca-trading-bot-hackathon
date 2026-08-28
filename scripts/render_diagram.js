const fs = require("fs");
const { Resvg } = require("@resvg/resvg-js");

const [, , input, output, zoomArg] = process.argv;
const zoom = Number(zoomArg || 2);

const resvg = new Resvg(fs.readFileSync(input, "utf8"), {
  fitTo: { mode: "zoom", value: zoom },
  background: "#FFFFFF",
  font: { loadSystemFonts: true, defaultFontFamily: "Segoe UI" },
});
const png = resvg.render().asPng();
fs.writeFileSync(output, png);
console.log(output + "  " + resvg.width * zoom + "x" + resvg.height * zoom + "  " + png.length + " bytes");
