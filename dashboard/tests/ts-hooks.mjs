// Node resolve hook for `npm test`: lets plain node (with type stripping) load the dashboard's
// TypeScript modules: extensionless relative imports ("./time" -> "./time.ts") and the
// tsconfig "@/..." alias (-> the dashboard root). Test-only; Next uses its own bundler.
const ROOT = new URL("../", import.meta.url);

export async function resolve(specifier, context, next) {
  let spec = specifier;
  if (spec.startsWith("@/")) spec = new URL(spec.slice(2), ROOT).href;
  const relative = spec.startsWith("./") || spec.startsWith("../") || spec.startsWith("file:");
  try {
    return await next(spec, context);
  } catch (err) {
    if (relative && !/\.[cm]?[jt]sx?$/.test(spec)) return next(spec + ".ts", context);
    throw err;
  }
}
