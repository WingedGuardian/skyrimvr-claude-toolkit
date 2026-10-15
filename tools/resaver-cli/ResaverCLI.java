import resaver.ess.ESS;
import resaver.ess.ModelBuilder;
import resaver.ess.RefID;
import resaver.ess.Plugin;
import resaver.ess.Element;
import resaver.ess.ChangeForm;
import resaver.ess.ChangeFormData;
import resaver.ess.GeneralElement;
import resaver.ess.ChangeFormExtraDataData;
import resaver.ess.GlobalVariable;
import resaver.ess.GlobalData;
import resaver.ess.GlobalDataBlock;
import resaver.ProgressModel;
import resaver.ess.ChangeFormACHR;
import resaver.ess.ChangeFormRefr;
import resaver.ess.ChangeFormFLST;
import resaver.ess.ChangeFormLeveled;
import resaver.ess.ChangeFormNPC;
import resaver.ess.ChangeFormRela;
import resaver.ess.ChangeFormQust;
import resaver.ess.ChangeFormInventoryItem;
import resaver.ess.papyrus.*;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.Buffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.LinkOption;
import java.nio.file.StandardOpenOption;
import java.nio.channels.FileChannel;
import java.security.MessageDigest;
import java.io.InputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.*;

/**
 * ResaverCLI — headless driver for ReSaver's (FallrimTools) save library. JSON on stdout.
 * Usage: ResaverCLI <op> <save.ess> [args...]
 *   info       <save>
 *   dump       <save> <subsystem> [--limit N] [--undefined-only] [--script <name>] [--type <T>]
 *              subsystem: scriptinstances|activescripts|references|structinstances|scripts|globals|changeforms
 *   changeform <save> <refidHex> [--depth N]   parse ONE ChangeForm body (default 00000014=player); bestEffort partial
 *   find-refs  <save> <eidHex>                 who references this element (direct + secondary, labeled)
 *   find       <save> <query>                  query = <Plugin.esp:formid> | <formidHex> | <script-name substring>
 *   worries    <save>                          ReSaver's Worrier problem report
 *   recon      <save>                          READ: sync-aware parse-coverage scan of ALL body types
 *                                              (perType ok/fail, failure categories, unknown extra-data
 *                                              types WITH predecessor histograms = the phantom test)
 *   set-global <save> <target> <value> [<out.ess>] [--apply]   target = formidHex | Plugin.esp:formid
 *   set-var    <save> <eidHex> [<index> <value> <out.ess>] [--type int|float|bool|str] [--apply]
 *   clean      <save> <out.ess> [--undefined] [--unattached] [--terminate-threads] [--apply]
 *
 * Writes NEVER overwrite the input; dry-run unless --apply; output goes to a NEW file.
 */
public class ResaverCLI {
    static InputIdentity inputIdentity;
    static ModelEnvelope modelBefore;
    static String activeOperation;
    static Path callerDirectory = Paths.get("").toAbsolutePath();

    public static void main(String[] args) {
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8));
        System.setErr(new PrintStream(System.err, true, StandardCharsets.UTF_8));
        java.util.logging.Logger.getLogger("").setLevel(java.util.logging.Level.SEVERE);
        try {
            if (args.length == 2 && args[0].equals("--arguments-file")) {
                // Windows' JDK launcher can lose characters outside the ANSI
                // code page. The wrapper passes only ASCII native arguments;
                // actual arguments and cwd travel as explicit UTF-8/base64 data.
                List<String> lines = Files.readAllLines(Paths.get(args[1]), StandardCharsets.US_ASCII);
                if (lines.isEmpty()) throw new IllegalArgumentException("empty wrapper argument file");
                Base64.Decoder decoder = Base64.getDecoder();
                callerDirectory = Paths.get(new String(decoder.decode(lines.get(0)), StandardCharsets.UTF_8));
                if (!callerDirectory.isAbsolute()) throw new IllegalArgumentException("wrapper cwd must be absolute");
                args = lines.subList(1, lines.size()).stream()
                        .map(line -> new String(decoder.decode(line), StandardCharsets.UTF_8)).toArray(String[]::new);
            }
            if (args.length < 2) { err("need <op> <save> [...]"); return; }
            String op = args[0];
            activeOperation = op;
            Path save = callerPath(args[1]);
            if (isWrite(op)) inputIdentity = new InputIdentity(save);
            ESS.Result result = ESS.readESS(save, new ModelBuilder(new ProgressModel(1)));
            ESS ess = result.ESS;
            Papyrus pap = ess.getPapyrus();
            PapyrusContext ctx = pap.getContext();
            if (inputIdentity != null) inputIdentity.verify();
            if (isWrite(op)) modelBefore = new ModelEnvelope(ess);

            switch (op) {
                case "info":       info(ess, pap); break;
                case "dump":       dump(ess, pap, ctx, args); break;
                case "changeform": changeform(ess, args); break;
                case "find-refs":  findRefs(ess, pap, ctx, arg(args, 2, null)); break;
                case "find":       find(ess, pap, ctx, arg(args, 2, null)); break;
                case "worries":    worries(result); break;
                case "set-global": setGlobal(ess, args); break;
                case "set-var":    setVar(ess, pap, ctx, args); break;
                case "clean":      clean(ess, pap, save, args); break;
                case "reset-havok":       resetHavokOp(ess, args); break;
                case "cleanse-formlists": cleanseFormListsOp(ess, args); break;
                case "remove-created":    removeCreatedOp(ess, args); break;
                case "verify-roundtrip":  verifyRoundtrip(ess); break;
                case "extradata-scan":    extradataScan(ess, args); break;
                case "recon":             recon(ess, args); break;
                case "changeform-diff":   changeformDiff(ess, args); break;
                case "freeze-report":     freezeReport(ess, pap); break;
                case "globaldata":        globalDataOp(ess); break;
                case "globaldata-diff":   globalDataDiff(ess, args); break;
                default: err("unknown op: " + op);
            }
        } catch (Throwable t) {
            System.out.println("{\"ok\":false,\"error\":" + jstr(t.toString()) + "}");
            t.printStackTrace(System.err);
            System.exit(t instanceof IllegalArgumentException ? 2 : 1);
        }
    }

    static Path callerPath(String value) {
        Path path = Paths.get(value);
        return (path.isAbsolute() ? path : callerDirectory.resolve(path)).normalize();
    }

    static boolean isWrite(String op) {
        return Arrays.asList("set-global", "set-var", "clean", "reset-havok", "cleanse-formlists", "remove-created").contains(op);
    }

    static Path cosavePath(Path ess) {
        String name = ess.getFileName().toString();
        if (!name.toLowerCase(Locale.ROOT).endsWith(".ess"))
            throw new IllegalArgumentException("save output must end in .ess");
        return ess.resolveSibling(name.substring(0, name.length() - 4) + ".skse");
    }

    static byte[] fileDigest(Path path) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream in = Files.newInputStream(path)) {
            byte[] buffer = new byte[1024 * 1024];
            for (int n; (n = in.read(buffer)) != -1;) digest.update(buffer, 0, n);
        }
        return digest.digest();
    }

    static final class InputIdentity {
        final Map<Path, byte[]> members = new LinkedHashMap<>();
        final Map<Path, String> identities = new LinkedHashMap<>();
        InputIdentity(Path save) throws Exception {
            Path absolute = save.toAbsolutePath().normalize();
            for (Path p : Arrays.asList(absolute, cosavePath(absolute))) {
                if (Files.isSymbolicLink(p)) throw new IllegalArgumentException("linked save input is not supported");
                members.put(p, Files.exists(p) ? fileDigest(p) : null);
                identities.put(p, Files.exists(p) ? fileIdentity(p) : null);
            }
        }
        void verify() throws Exception {
            for (Map.Entry<Path, byte[]> e : members.entrySet()) {
                if (Files.isSymbolicLink(e.getKey()) || (e.getValue() == null
                        ? Files.exists(e.getKey(), LinkOption.NOFOLLOW_LINKS)
                        : !Files.exists(e.getKey()) || !Objects.equals(identities.get(e.getKey()), fileIdentity(e.getKey()))
                          || !Arrays.equals(e.getValue(), fileDigest(e.getKey()))
                          || !Objects.equals(identities.get(e.getKey()), fileIdentity(e.getKey()))))
                    throw new IllegalStateException("input save membership/bytes changed: " + e.getKey());
            }
        }
    }

    static String fileIdentity(Path path) throws Exception {
        BasicFileAttributes a = Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        return path.toRealPath() + "|" + a.fileKey() + "|" + a.size() + "|" + a.lastModifiedTime();
    }

    static String bytesDigest(byte[] bytes) throws Exception {
        return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    /** Baseline-to-model fence, separate from model-to-readback verification. */
    static final class ModelEnvelope {
        final Map<String, String> tables = new LinkedHashMap<>();
        final Map<String, String> forms = new LinkedHashMap<>();
        final Map<String, String> types = new HashMap<>();
        final Map<String, Float> globals;
        final int[] formids, visited;
        final byte[] cosave;
        ModelEnvelope(ESS ess) throws Exception {
            for (String name : new String[]{"TABLE1", "TABLE2", "TABLE3"}) {
                List<GlobalData> entries = gdTable(ess, name);
                if (entries == null) throw new IllegalStateException("global table unavailable: " + name);
                for (int i = 0; i < entries.size(); i++) {
                    GlobalData g = entries.get(i);
                    tables.put(name + ":" + i + ":" + g.getType(), bytesDigest(completeGlobalBytes(g)));
                }
            }
            for (ChangeForm f : ess.getChangeForms()) {
                String key = str(f.getRefID());
                forms.put(key, str(f.getChangeFlags()) + ":" + f.getVersion() + ":" + bytesDigest(cfBytes(f)));
                types.put(key, str(f.getType()));
            }
            globals = globalsMap(ess);
            formids = ess.getFormIDs().clone();
            visited = ess.getVisitedWorldspaceArray().clone();
            cosave = ess.getCosave() == null ? null : ess.getCosave().clone();
        }
        void verifyAfter(ESS ess, String operation) throws Exception {
            ModelEnvelope after = new ModelEnvelope(ess);
            if (!tables.keySet().equals(after.tables.keySet()) || !forms.keySet().equals(after.forms.keySet()))
                throw new IllegalStateException("operation unexpectedly added/removed global blocks or ChangeForms");
            String permittedTable = operation.equals("set-global") ? ":3"
                    : Arrays.asList("set-var", "clean", "remove-created").contains(operation) ? ":1001" : "none";
            for (String key : tables.keySet())
                if (!tables.get(key).equals(after.tables.get(key)) && !key.endsWith(permittedTable))
                    throw new IllegalStateException("operation changed an unrelated global block: " + key);
            String permittedForm = operation.equals("reset-havok") ? "REFR"
                    : operation.equals("cleanse-formlists") ? "FLST" : "none";
            for (String key : forms.keySet())
                if (!forms.get(key).equals(after.forms.get(key)) && !permittedForm.equals(types.get(key)))
                    throw new IllegalStateException("operation changed an unrelated ChangeForm: " + key);
            if (!operation.equals("set-global") && !globals.equals(after.globals))
                throw new IllegalStateException("operation changed unrelated globals");
            if (operation.equals("set-global")) {
                if (!globals.keySet().equals(after.globals.keySet()))
                    throw new IllegalStateException("set-global changed global identities");
                long changed = globals.keySet().stream().filter(k -> !globals.get(k).equals(after.globals.get(k))).count();
                if (changed > 1) throw new IllegalStateException("set-global changed more than one global");
            }
            if (!Arrays.equals(formids, after.formids) || !Arrays.equals(visited, after.visited)
                    || !Arrays.equals(cosave, after.cosave))
                throw new IllegalStateException("operation changed FormID/worldspace arrays or cosave bytes");
        }
    }

    // ---------- info ----------

    static void info(ESS ess, Papyrus pap) {
        int[] undef = pap.countUndefinedElements();
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"info\"");
        kv(b, "game", ess.getHeader().GAME.toString());
        kvn(b, "changeForms", ess.getChangeForms().size());
        kvn(b, "scriptInstances", pap.getScriptInstances().size());
        kvn(b, "scripts", pap.getScripts().size());
        kvn(b, "references", pap.getReferences().size());
        kvn(b, "structInstances", pap.getStructInstances().size());
        kvn(b, "arrays", pap.getArrays().size());
        kvn(b, "activeScripts", pap.getActiveScripts().size());
        kvn(b, "suspendedStacks", pap.getSuspendedStacks().size());
        kvn(b, "unbinds", pap.getUnbinds().size());
        kvn(b, "functionMessages", pap.getFunctionMessages().size());
        // [0] = undefined Scripts+ScriptInstances+References+Structs+StructInstances; [1] = undefined non-terminated threads
        kvn(b, "undefinedElements", undef.length > 0 ? undef[0] : -1);
        kvn(b, "undefinedThreads", undef.length > 1 ? undef[1] : -1);
        kvn(b, "unattachedInstances", pap.countUnattachedInstances());
        b.append("}");
        System.out.println(b);
    }

    // ---------- dump ----------

    static void dump(ESS ess, Papyrus pap, PapyrusContext ctx, String[] args) {
        String sub = arg(args, 2, "scriptinstances");
        int limit = flagVal(args, "--limit") != null ? Integer.parseInt(flagVal(args, "--limit")) : Integer.MAX_VALUE;
        boolean undefOnly = has(args, "--undefined-only");
        String scriptFilter = flagVal(args, "--script");
        String typeFilter = flagVal(args, "--type");
        String sf = scriptFilter == null ? null : scriptFilter.toLowerCase();

        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"dump\",\"subsystem\":").append(jstr(sub)).append(",\"items\":[");
        int n = 0;
        switch (sub.toLowerCase()) {
            case "scriptinstances":
                for (ScriptInstance si : pap.getScriptInstances().values()) {
                    if (undefOnly && !si.isUndefined()) continue;
                    if (sf != null && (si.getScript() == null || !str(si.getScript().getName()).toLowerCase().contains(sf))) continue;
                    if (n >= limit) break;
                    if (n++ > 0) b.append(","); b.append(siJson(si));
                }
                break;
            case "activescripts":
                for (ActiveScript a : pap.getActiveScripts().values()) {
                    if (undefOnly && !a.isUndefined()) continue;
                    if (n >= limit) break;
                    if (n++ > 0) b.append(",");
                    b.append("{\"id\":").append(jstr(str(a.getID())))
                     .append(",\"undefined\":").append(a.isUndefined())
                     .append(",\"terminated\":").append(a.isTerminated())
                     .append(",\"attached\":").append(jstr(str(a.getAttachedElement())))
                     .append(",\"instance\":").append(jstr(str(a.getInstance()))).append("}");
                }
                break;
            case "references":
                for (Reference r : pap.getReferences().values()) {
                    if (undefOnly && !r.isUndefined()) continue;
                    if (sf != null && (r.getScript() == null || !str(r.getScript().getName()).toLowerCase().contains(sf))) continue;
                    if (n >= limit) break;
                    if (n++ > 0) b.append(",");
                    b.append("{\"id\":").append(jstr(str(r.getID())))
                     .append(",\"script\":").append(jstr(r.getScript()==null?null:str(r.getScript().getName())))
                     .append(",\"undefined\":").append(r.isUndefined())
                     .append(",\"vars\":").append(r.getVariables().size()).append("}");
                }
                break;
            case "structinstances":
                for (StructInstance s : pap.getStructInstances().values()) {
                    if (undefOnly && !s.isUndefined()) continue;
                    if (n >= limit) break;
                    if (n++ > 0) b.append(",");
                    b.append("{\"id\":").append(jstr(str(s.getID())))
                     .append(",\"struct\":").append(jstr(s.getStruct()==null?null:str(s.getStruct().getName())))
                     .append(",\"undefined\":").append(s.isUndefined()).append("}");
                }
                break;
            case "scripts":
                for (Script s : pap.getScripts().values()) {
                    if (undefOnly && !s.isUndefined()) continue;
                    if (sf != null && !str(s.getName()).toLowerCase().contains(sf)) continue;
                    if (n >= limit) break;
                    if (n++ > 0) b.append(",");
                    b.append("{\"name\":").append(jstr(str(s.getName())))
                     .append(",\"undefined\":").append(s.isUndefined())
                     .append(",\"members\":").append(s.getMembers()==null?0:s.getMembers().size()).append("}");
                }
                break;
            case "globals":
                for (GlobalVariable g : ess.getGlobals().getVariables()) {
                    if (n >= limit) break;
                    if (n++ > 0) b.append(",");
                    b.append("{\"global\":").append(jstr(str(g)))
                     .append(",\"value\":").append(g.getValue()).append("}");
                }
                break;
            case "changeforms":
                for (ChangeForm cf : ess.getChangeForms()) {
                    String t = str(cf.getType());
                    if (typeFilter != null && (t == null || !t.equalsIgnoreCase(typeFilter))) continue;
                    if (n >= limit) break;
                    if (n++ > 0) b.append(",");
                    b.append("{\"refid\":").append(jstr(refStr(cf.getRefID())))
                     .append(",\"type\":").append(jstr(t))
                     .append(",\"version\":").append(cf.getVersion())
                     .append(",\"compressed\":").append(cf.isCompressed()).append("}");
                }
                break;
            default:
                b.append("]}"); System.out.println(b); err("unknown subsystem: " + sub); return;
        }
        b.append("],\"count\":").append(n);
        if (n >= limit) b.append(",\"truncated\":true");
        b.append("}");
        System.out.println(b);
    }

    // ---------- changeform (parse one ChangeForm BODY; bestEffort=true returns a partial) ----------

    static void changeform(ESS ess, String[] args) {
        String refHex = arg(args, 2, "00000014");          // default = the player
        Long fid = parseHex(refHex);
        int maxDepth = flagVal(args, "--depth") != null ? Integer.parseInt(flagVal(args, "--depth")) : 8;
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"changeform\",\"refid\":").append(jstr(refHex));
        if (fid == null) { b.append(",\"error\":\"bad refid hex\"}"); System.out.println(b); return; }

        // collect refid matches; prefer the ACHR (actor) form when several share a FormID
        List<ChangeForm> matches = new ArrayList<>();
        for (ChangeForm cf : ess.getChangeForms()) if (refidMatches(cf.getRefID(), null, fid)) matches.add(cf);
        ChangeForm chosen = null;
        for (ChangeForm cf : matches) if ("ACHR".equalsIgnoreCase(str(cf.getType()))) { chosen = cf; break; }
        if (chosen == null && !matches.isEmpty()) chosen = matches.get(0);
        if (chosen == null) { b.append(",\"found\":false}"); System.out.println(b); return; }

        b.append(",\"found\":true,\"actualRefid\":").append(jstr(refStr(chosen.getRefID())))
         .append(",\"type\":").append(jstr(str(chosen.getType())))
         .append(",\"matchCount\":").append(matches.size())
         .append(",\"compressed\":").append(chosen.isCompressed());

        // --raw: emit decoded changeflags + raw body bytes (for diff inputs / read-only RE analysis)
        if (has(args, "--raw")) {
            b.append(",\"changeFlags\":").append(jstr(str(chosen.getChangeFlags())));
            ByteBuffer rawBody = chosen.getBodyData();
            if (rawBody != null) {
                ((Buffer) rawBody).position(0);
                byte[] raw = new byte[rawBody.remaining()]; rawBody.get(raw);
                b.append(",\"bodyLength\":").append(raw.length)
                 .append(",\"bodyHex\":").append(jstr(hex(raw, 0, raw.length)))
                 .append(",\"bodyBase64\":").append(jstr(java.util.Base64.getEncoder().encodeToString(raw)));
            } else b.append(",\"bodyLength\":0");
        }

        // bestEffort=true: returns the PARTIAL parse (via ElementException.getPartial) instead of null
        ChangeFormData data = null; String parseErr = null;
        try { data = chosen.getData(Optional.empty(), ess.getContext(), true); }
        catch (Throwable t) { parseErr = t.toString(); }

        if (data == null) {
            b.append(",\"parsed\":false,\"parseError\":")
             .append(jstr(parseErr == null ? "getData returned null (not bestEffort-recoverable)" : parseErr))
             .append("}");
            System.out.println(b); return;
        }
        b.append(",\"parsed\":true,\"dataClass\":").append(jstr(data.getClass().getSimpleName()));
        if (data instanceof GeneralElement) {
            GeneralElement ge = (GeneralElement) data;
            b.append(",\"hasUnparsed\":").append(ge.hasUnparsed());
            b.append(",\"tree\":");
            generalElementToJson(b, ge, maxDepth, new int[]{0});
        } else {
            b.append(",\"tree\":").append(jstr(str(data)));
        }
        b.append("}");
        System.out.println(b);
    }

    static final int CF_NODE_CAP = 20000;   // guard against pathological trees

    static void generalElementToJson(StringBuilder b, GeneralElement ge, int depth, int[] nodes) {
        b.append("{");
        // surface the human-readable extra-data type name (e.g. "MagicTarget", "Worn", "Enchantment")
        if (ge instanceof ChangeFormExtraDataData) {
            b.append("\"_extraType\":").append(jstr(((ChangeFormExtraDataData) ge).NAME));
        }
        boolean first = !(ge instanceof ChangeFormExtraDataData);
        for (Map.Entry<?, ?> e : ge.getValues().entrySet()) {
            if (nodes[0]++ > CF_NODE_CAP) { if (!first) b.append(","); b.append("\"_capped\":true"); break; }
            if (!first) b.append(","); first = false;
            b.append(jstr(String.valueOf(e.getKey()))).append(":");
            valueToJson(b, e.getValue(), depth, nodes);
        }
        b.append("}");
    }

    static void valueToJson(StringBuilder b, Object v, int depth, int[] nodes) {
        if (v == null) { b.append("null"); return; }
        if (v instanceof GeneralElement) {
            if (depth <= 0) { b.append(jstr("<" + v.getClass().getSimpleName() + " depth-capped>")); return; }
            generalElementToJson(b, (GeneralElement) v, depth - 1, nodes); return;
        }
        if (v instanceof Object[]) {
            Object[] a = (Object[]) v; b.append("[");
            for (int i = 0; i < a.length; i++) { if (i > 0) b.append(","); valueToJson(b, a[i], depth, nodes); }
            b.append("]"); return;
        }
        if (v instanceof byte[])  { byte[] a=(byte[])v;  b.append("["); for(int i=0;i<a.length;i++){if(i>0)b.append(",");b.append(a[i]&0xFF);} b.append("]"); return; }
        if (v instanceof short[]) { short[] a=(short[])v;b.append("["); for(int i=0;i<a.length;i++){if(i>0)b.append(",");b.append(a[i]);} b.append("]"); return; }
        if (v instanceof int[])   { int[] a=(int[])v;    b.append("["); for(int i=0;i<a.length;i++){if(i>0)b.append(",");b.append(a[i]);} b.append("]"); return; }
        if (v instanceof long[])  { long[] a=(long[])v;  b.append("["); for(int i=0;i<a.length;i++){if(i>0)b.append(",");b.append(a[i]);} b.append("]"); return; }
        if (v instanceof float[]) { float[] a=(float[])v;b.append("["); for(int i=0;i<a.length;i++){if(i>0)b.append(",");b.append(a[i]);} b.append("]"); return; }
        if (v instanceof Number || v instanceof Boolean) { b.append(v.toString()); return; }
        b.append(jstr(v.toString()));   // RefID / WStringElement / VSVal / etc.
    }

    // ---------- find-refs (direct + secondary, labeled) ----------

    static void findRefs(ESS ess, Papyrus pap, PapyrusContext ctx, String eidHex) {
        if (eidHex == null) { err("find-refs needs <eidHex>"); return; }
        HasID target = resolveEID(pap, ctx, eidHex);
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"find-refs\",\"target\":").append(jstr(eidHex));
        if (target == null) { b.append(",\"found\":false}"); System.out.println(b); return; }
        b.append(",\"found\":true,\"targetType\":").append(jstr(target.getClass().getSimpleName()))
         .append(",\"targetDesc\":").append(jstr(str(target)));

        Set<Element> direct = directReferrers(pap, (Element) target);
        List<DefinedElement> all = ctx.findReferees((Element) target);  // direct + secondary (2-hop)
        int directCount = 0, secondaryCount = 0;
        b.append(",\"referees\":[");
        int n = 0;
        for (DefinedElement de : all) {
            boolean isDirect = direct.contains(de);
            if (isDirect) directCount++; else secondaryCount++;
            if (n++ > 0) b.append(",");
            b.append("{\"relation\":").append(isDirect ? "\"direct\"" : "\"secondary\"")
             .append(",\"type\":").append(jstr(de.getClass().getSimpleName()))
             .append(",\"desc\":").append(jstr(str(de))).append("}");
        }
        b.append("],\"refereeCount\":").append(n)
         .append(",\"directCount\":").append(directCount)
         .append(",\"secondaryCount\":").append(secondaryCount).append("}");
        System.out.println(b);
    }

    static Set<Element> directReferrers(Papyrus pap, Element target) {
        Set<Element> direct = new HashSet<>();
        for (ScriptInstance si : pap.getScriptInstances().values()) if (refersTo(si, target)) direct.add(si);
        for (Reference r : pap.getReferences().values())           if (refersTo(r, target))  direct.add(r);
        for (StructInstance s : pap.getStructInstances().values()) if (refersTo(s, target))  direct.add(s);
        for (ActiveScript a : pap.getActiveScripts().values())     if (Objects.equals(target, a.getAttachedElement())) direct.add(a);
        return direct;
    }
    static boolean refersTo(HasVariables hv, Element target) {
        for (Variable v : hv.getVariables()) if (v.hasRef() && Objects.equals(target, v.getReferent())) return true;
        return false;
    }

    // ---------- find (whole-save sweep) ----------

    static void find(ESS ess, Papyrus pap, PapyrusContext ctx, String query) {
        if (query == null) { err("find needs <query>"); return; }
        // Parse "Plugin.es[pml]:formid" | bare formid hex | script-name substring
        String plug = null; Long fid = null;
        java.util.regex.Matcher m = java.util.regex.Pattern
            .compile("^(.+\\.es[plm]):([0-9a-fA-F]+)$", java.util.regex.Pattern.CASE_INSENSITIVE).matcher(query);
        if (m.matches()) { plug = m.group(1); fid = parseHex(m.group(2)); }
        else if (query.matches("^(0x)?[0-9a-fA-F]{1,8}$")) { fid = parseHex(query); }
        String q = query.toLowerCase();

        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"find\",\"query\":").append(jstr(query));
        b.append(",\"mode\":").append(jstr(fid != null ? (plug != null ? "plugin:formid" : "formid(any-plugin)") : "scriptName"));
        b.append(",\"hits\":[");
        int n = 0;

        for (ScriptInstance si : pap.getScriptInstances().values()) {
            boolean hit = (fid != null) ? refidMatches(si.getRefID(), plug, fid)
                                        : (si.getScript()!=null && str(si.getScript().getName()).toLowerCase().contains(q));
            if (hit) { if (n++>0) b.append(","); b.append("{\"kind\":\"scriptInstance\",").append(siBody(si)).append("}"); }
        }
        if (fid == null) {
            for (Reference r : pap.getReferences().values())
                if (r.getScript()!=null && str(r.getScript().getName()).toLowerCase().contains(q)) {
                    if (n++>0) b.append(","); b.append("{\"kind\":\"reference\",\"id\":").append(jstr(str(r.getID()))).append(",\"script\":").append(jstr(str(r.getScript().getName()))).append("}");
                }
            for (Script s : pap.getScripts().values())
                if (str(s.getName()).toLowerCase().contains(q)) {
                    if (n++>0) b.append(","); b.append("{\"kind\":\"script\",\"name\":").append(jstr(str(s.getName()))).append(",\"undefined\":").append(s.isUndefined()).append("}");
                }
        } else {
            for (ChangeForm cf : ess.getChangeForms())
                if (refidMatches(cf.getRefID(), plug, fid)) {
                    if (n++>0) b.append(","); b.append("{\"kind\":\"changeForm\",\"refid\":").append(jstr(refStr(cf.getRefID()))).append(",\"type\":").append(jstr(str(cf.getType()))).append("}");
                }
        }
        b.append("],\"hitCount\":").append(n).append("}");
        System.out.println(b);
    }

    static boolean refidMatches(RefID r, String plug, Long fid) {
        if (r == null || fid == null) return false;
        if ((r.FORMID & 0xFFFFFF) != (fid.intValue() & 0xFFFFFF)) return false;
        if (plug != null) return r.PLUGIN != null && r.PLUGIN.NAME.equalsIgnoreCase(plug);
        return true;
    }

    // ---------- worries ----------

    static void worries(ESS.Result result) {
        Worrier w = new Worrier(result, Optional.empty());
        String html = w.getMessage().render();
        String text = html.replaceAll("(?i)<br\\s*/?>", "\n").replaceAll("(?i)</(p|div|h1|h2|h3|li|hr)>", "\n")
                          .replaceAll("<[^>]+>", "").replaceAll("&nbsp;", " ").replaceAll("&amp;", "&")
                          .replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("\\n{3,}", "\n\n").trim();
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"worries\"")
         .append(",\"shouldWorry\":").append(w.shouldWorry())
         .append(",\"shouldDisableSaving\":").append(w.shouldDisableSaving())
         .append(",\"reportText\":").append(jstr(text))
         .append(",\"reportHtml\":").append(jstr(html))
         .append("}");
        System.out.println(b);
    }

    // ---------- set-global ----------

    static void setGlobal(ESS ess, String[] args) throws Exception {
        List<String> pos = positionals(args, 2);   // [target, value, out?]
        if (pos.size() < 2) { err("set-global needs <target> <value> [<out.ess>] [--apply]"); return; }
        String target = pos.get(0);
        float newVal = Float.parseFloat(pos.get(1));
        if (!Float.isFinite(newVal)) throw new IllegalArgumentException("global value must be finite");
        boolean apply = has(args, "--apply");
        String out = pos.size() > 2 ? pos.get(2) : null;

        GlobalVariable match = null;
        for (GlobalVariable g : ess.getGlobals().getVariables()) {
            if (globalMatches(g, target)) { match = g; break; }
        }
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"set-global\",\"target\":").append(jstr(target)).append(",\"apply\":").append(apply);
        if (match == null) {
            if (apply) { err("global target not found; nothing was written"); return; }
            b.append(",\"found\":false}"); System.out.println(b); return;
        }
        b.append(",\"found\":true,\"global\":").append(jstr(str(match)))
         .append(",\"oldValue\":").append(match.getValue()).append(",\"newValue\":").append(newVal);
        if (!apply) { b.append(",\"dryRun\":true}"); System.out.println(b); return; }
        if (out == null) { err("--apply needs <out.ess>"); return; }
        match.setValue(newVal);
        finishWrite(b, ess, apply, out);
    }

    // toString() = "([name] )?PLUGIN:FORMIDHEX = value"; match target = formidHex | Plugin:formid
    static boolean globalMatches(GlobalVariable g, String target) {
        String s = str(g);
        int eq = s.indexOf(" = ");
        if (eq < 0) return false;
        String idTok = s.substring(0, eq).trim();
        int rb = idTok.lastIndexOf("] ");
        if (rb >= 0) idTok = idTok.substring(rb + 2);     // strip "[name] "
        // idTok now "PLUGIN:FORMIDHEX"
        if (target.contains(":")) return idTok.equalsIgnoreCase(target);
        int c = idTok.indexOf(':');
        String fhex = c >= 0 ? idTok.substring(c + 1) : idTok;
        Long a = parseHex(fhex), t = parseHex(target);
        return a != null && t != null && (a.intValue() & 0xFFFFFF) == (t.intValue() & 0xFFFFFF);
    }

    // ---------- set-var (Papyrus instance variable) ----------

    static void setVar(ESS ess, Papyrus pap, PapyrusContext ctx, String[] args) throws Exception {
        List<String> pos = positionals(args, 2);   // [eid] (preview) | [eid, index, value, out?]
        if (pos.isEmpty()) { err("set-var needs <eidHex> [<index> <value> <out.ess>]"); return; }
        String eidHex = pos.get(0);
        HasID target = resolveEID(pap, ctx, eidHex);
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"set-var\",\"target\":").append(jstr(eidHex));
        if (!(target instanceof HasVariables)) {
            err("target not found or has no variables"); return;
        }
        HasVariables hv = (HasVariables) target;
        List<Variable> vars = hv.getVariables();
        b.append(",\"found\":true,\"targetType\":").append(jstr(target.getClass().getSimpleName()));

        if (pos.size() < 3) {   // preview mode: list variables
            b.append(",\"preview\":true,\"variables\":[");
            for (int i = 0; i < vars.size(); i++) {
                if (i > 0) b.append(",");
                Variable v = vars.get(i);
                b.append("{\"index\":").append(i).append(",\"type\":").append(jstr(str(v.getType())))
                 .append(",\"value\":").append(jstr(str(v))).append("}");
            }
            b.append("]}"); System.out.println(b); return;
        }

        int index = Integer.parseInt(pos.get(1));
        String val = pos.get(2);
        boolean apply = has(args, "--apply");
        String out = pos.size() > 3 ? pos.get(3) : null;
        if (index < 0 || index >= vars.size()) { err("index out of range"); return; }
        Variable old = vars.get(index);
        String type = flagVal(args, "--type");
        Variable nv = buildVar(old, type, val, ctx);
        if (nv == null) { err("unsupported var type (primitives int/float/bool/str only)"); return; }
        b.append(",\"index\":").append(index).append(",\"oldValue\":").append(jstr(str(old)))
         .append(",\"newValue\":").append(jstr(str(nv))).append(",\"apply\":").append(apply);
        if (!apply) { b.append(",\"dryRun\":true}"); System.out.println(b); return; }
        if (out == null) { err("--apply needs <out.ess>"); return; }
        hv.setVariable(index, nv);
        finishWrite(b, ess, apply, out);
    }

    static Variable buildVar(Variable old, String type, String val, PapyrusContext ctx) {
        String t = type != null ? type.toLowerCase()
                 : (old instanceof Variable.Int ? "int" : old instanceof Variable.Flt ? "float"
                  : old instanceof Variable.Bool ? "bool" : old instanceof Variable.Str ? "str" : null);
        if (t == null) return null;
        switch (t) {
            case "int":   return new Variable.Int(Integer.decode(val));
            case "float": return new Variable.Flt(Float.parseFloat(val));
            case "bool":  return new Variable.Bool(val.equals("1") || Boolean.parseBoolean(val));
            case "str":   return new Variable.Str(val, ctx);
            default:      return null;
        }
    }

    // ---------- clean ----------

    static void clean(ESS ess, Papyrus pap, Path inSave, String[] args) throws Exception {
        boolean undef = has(args, "--undefined");
        boolean unatt = has(args, "--unattached");
        boolean term  = has(args, "--terminate-threads");
        boolean apply = has(args, "--apply");
        String out = positionals(args, 2).isEmpty() ? null : positionals(args, 2).get(0);
        if (!undef && !unatt && !term) { err("clean needs at least one of --undefined/--unattached/--terminate-threads"); return; }

        int[] before = pap.countUndefinedElements();
        int unattBefore = pap.countUnattachedInstances();
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"clean\",\"apply\":").append(apply);
        kvn(b, "undefinedElementsBefore", before.length>0?before[0]:-1);
        kvn(b, "undefinedThreadsBefore", before.length>1?before[1]:-1);
        kvn(b, "unattachedBefore", unattBefore);

        if (!apply) {
            // NOTE: removeUndefinedElements() ALSO zeroes undefined threads internally, so --undefined already
            // covers thread termination; --terminate-threads is only additive if used alone.
            b.append(",\"dryRun\":true,\"wouldRemove\":{");
            b.append("\"undefinedElements\":").append(undef? (before.length>0?before[0]:0):0);
            b.append(",\"undefinedThreads\":").append((undef||term)? (before.length>1?before[1]:0):0);
            b.append(",\"unattached\":").append(unatt? unattBefore:0);
            b.append("},\"note\":\"--undefined also zeroes undefined threads\"}");
            System.out.println(b); return;
        }

        int rUndef=0, rUnatt=0, rTerm=0;
        if (unatt) rUnatt = pap.removeUnattachedInstances().size();
        if (undef) rUndef = pap.removeUndefinedElements().size();
        if (term)  rTerm  = pap.terminateUndefinedThreads().size();
        if (out == null) { err("clean --apply needs <out.ess>"); return; }
        b.append(",\"removedUnattached\":").append(rUnatt)
         .append(",\"removedUndefined\":").append(rUndef)
         .append(",\"terminatedThreads\":").append(rTerm);
        finishWrite(b, ess, apply, out);
    }

    // ========== CORRUPTION-SAFETY CORE ==========
    // We author NO serialization. Writes delegate to ReSaver's own ESS methods + ESS.writeESS.
    // Every --apply is VERIFY-GATED: re-read the output and confirm it equals the in-memory model
    // that produced it; on ANY divergence retain the owned candidate and fail. Existing destinations stay intact.
    // ChangeForm.write() emits original rawData verbatim unless updateRawData() was
    // explicitly called (so unmodified forms are byte-faithful by construction).

    /** Op-agnostic invariant: re-read(output) MUST equal the in-memory model that wrote it.
     *  Returns null if identical, else a human-readable divergence summary. */
    static String compareModels(ESS expected, ESS actual) {
        StringBuilder d = new StringBuilder();
        Map<String,ChangeForm> ma = indexCF(expected), mb = indexCF(actual);
        if (ma.size() != mb.size()) d.append("changeFormCount ").append(ma.size()).append("->").append(mb.size()).append("; ");
        int bodyDiff=0, typeDiff=0, onlyA=0, onlyB=0, readErr=0;
        Set<String> all = new TreeSet<>(); all.addAll(ma.keySet()); all.addAll(mb.keySet());
        for (String k : all) {
            ChangeForm fa = ma.get(k), fb = mb.get(k);
            if (fa == null) { onlyB++; continue; }
            if (fb == null) { onlyA++; continue; }
            if (!Objects.equals(str(fa.getType()), str(fb.getType()))) { typeDiff++; continue; }
            byte[] xa, xb;
            try { xa = cfBytes(fa); xb = cfBytes(fb); } catch (Throwable t) { readErr++; continue; }
            if (!Arrays.equals(xa, xb)) bodyDiff++;
        }
        if (bodyDiff>0) d.append("changeformBodyDiff=").append(bodyDiff).append("; ");
        if (typeDiff>0) d.append("changeformTypeDiff=").append(typeDiff).append("; ");
        if (onlyA>0) d.append("onlyInExpected=").append(onlyA).append("; ");
        if (onlyB>0) d.append("onlyInActual=").append(onlyB).append("; ");
        if (readErr>0) d.append("bodyReadErrors=").append(readErr).append("; ");
        Map<String,Float> ga = globalsMap(expected), gb = globalsMap(actual);
        if (!ga.keySet().equals(gb.keySet())) d.append("global identities differ; ");
        int gDiff=0; for (String k : ga.keySet()) { Float vb = gb.get(k); if (vb!=null && !ga.get(k).equals(vb)) gDiff++; }
        if (gDiff>0) d.append("globalValueDiff=").append(gDiff).append("; ");
        Papyrus pa = expected.getPapyrus(), pb = actual.getPapyrus();
        if (pa.getScriptInstances().size()!=pb.getScriptInstances().size()) d.append("scriptInstances ").append(pa.getScriptInstances().size()).append("->").append(pb.getScriptInstances().size()).append("; ");
        if (pa.getReferences().size()!=pb.getReferences().size()) d.append("references ").append(pa.getReferences().size()).append("->").append(pb.getReferences().size()).append("; ");
        if (pa.getActiveScripts().size()!=pb.getActiveScripts().size()) d.append("activeScripts ").append(pa.getActiveScripts().size()).append("->").append(pb.getActiveScripts().size()).append("; ");
        return d.length()==0 ? null : d.toString();
    }

    static Map<String,ChangeForm> indexCF(ESS e) { Map<String,ChangeForm> m=new HashMap<>(); for (ChangeForm c : e.getChangeForms()) m.put(str(c.getRefID()), c); return m; }
    static byte[] cfBytes(ChangeForm c) { ByteBuffer b=c.getBodyData(); if (b==null) return new byte[0]; ((Buffer)b).position(0); byte[] r=new byte[b.remaining()]; b.get(r); return r; }
    static Map<String,Float> globalsMap(ESS e) { Map<String,Float> m=new HashMap<>(); for (GlobalVariable g : e.getGlobals().getVariables()){ String s=str(g); int i=s.indexOf(" = "); m.put(i<0?s:s.substring(0,i), g.getValue()); } return m; }

    /** Re-read an owned candidate and compare it with the model. Never delete paths supplied by a caller. */
    static String verifyWrite(ESS expected, Path outPath) {
        try {
            ESS reread = ESS.readESS(outPath, new ModelBuilder(new ProgressModel(1))).ESS;
            // The pinned library compares complete serialized Papyrus and cosave
            // bytes using explicit exceptions, independent of JVM assertions.
            ESS.verifyIdentical(expected, reread);
            String diff = compareModels(expected, reread);
            if (diff != null) return diff;
            for (String table : new String[]{"TABLE1", "TABLE2", "TABLE3"}) {
                List<GlobalData> a = gdTable(expected, table), c = gdTable(reread, table);
                if (a == null || c == null || a.size() != c.size()) return table + " unavailable or count differs";
                for (int i = 0; i < a.size(); i++)
                    if (!Arrays.equals(completeGlobalBytes(a.get(i)), completeGlobalBytes(c.get(i))))
                        return table + " serialized block differs at " + i;
            }
            return null;
        } catch (Throwable t) {
            return "re-read FAILED: " + t;
        }
    }

    static byte[] completeGlobalBytes(GlobalData block) {
        ByteBuffer data = ByteBuffer.allocate(block.calculateSize()).order(ByteOrder.LITTLE_ENDIAN);
        block.write(data);
        if (data.hasRemaining()) throw new IllegalStateException("global block serialized an unexpected size");
        return data.array();
    }

    static void requireNewPair(Path output) throws Exception {
        for (Path p : Arrays.asList(output, cosavePath(output)))
            if (Files.exists(p, LinkOption.NOFOLLOW_LINKS))
                throw new IllegalArgumentException("refusing existing destination pair member: " + p);
    }

    static void syncFile(Path path) throws Exception {
        try (FileChannel file = FileChannel.open(path, StandardOpenOption.WRITE)) { file.force(true); }
    }

    static void publishNew(Path source, Path target) throws Exception {
        // createLink is exclusive on supported filesystems. No check-then-rename
        // fallback: an unsupported filesystem must refuse rather than clobber.
        Files.createLink(target, source);
        Files.delete(source);
    }

    /** Shared dry-run/apply tail with the MANDATORY verify gate. */
    static void finishWrite(StringBuilder b, ESS ess, boolean apply, String out) throws Exception {
        if (!apply) { b.append(",\"dryRun\":true,\"wroteFile\":false}"); System.out.println(b); return; }
        if (out == null) { err("--apply needs <out.ess>"); return; }
        Path outPath = callerPath(out);
        requireNewPair(outPath);
        if (inputIdentity == null) throw new IllegalStateException("write input identity was not captured");
        if (modelBefore == null) throw new IllegalStateException("write model baseline was not captured");
        modelBefore.verifyAfter(ess, activeOperation);
        inputIdentity.verify();
        Files.createDirectories(outPath.getParent());
        Path stage = Files.createTempDirectory(outPath.getParent(), ".resaver-staging-");
        Path candidate = stage.resolve(outPath.getFileName());
        List<String> published = new ArrayList<>();
        List<Path> journal = new ArrayList<>();
        try {
            Path plan = stage.resolve("TRANSACTION.json");
            Files.writeString(plan, "{\"op\":" + jstr(activeOperation) + ",\"destination\":" + jstr(outPath.toString()) + "}",
                              StandardOpenOption.CREATE_NEW);
            syncFile(plan);
            journal.add(plan);
            ESS.writeESS(ess, candidate, false);
            syncFile(candidate);
            if (Files.exists(cosavePath(candidate))) syncFile(cosavePath(candidate));
            String diff = verifyWrite(ess, candidate);
            if (diff != null) throw new IllegalStateException("candidate verification failed: " + diff);
            inputIdentity.verify();
            requireNewPair(outPath);
            // Both members have been checked before either can become visible.
            // Publication is per file; a concurrent creator can cause a partial
            // publication, which is recorded and never labelled successful.
            for (Path member : Arrays.asList(candidate, cosavePath(candidate))) {
                if (!Files.exists(member)) continue;
                Path destination = outPath.resolveSibling(member.getFileName());
                publishNew(member, destination);
                published.add(destination.toString());
                Path progress = stage.resolve("published-" + published.size() + ".json");
                Files.writeString(progress, "{\"path\":" + jstr(destination.toString()) + "}", StandardOpenOption.CREATE_NEW);
                syncFile(progress);
                journal.add(progress);
            }
            for (Path event : journal) Files.delete(event);
            Files.delete(stage);
        } catch (Exception failure) {
            String note = "failed candidate retained at " + stage + "; published=" + published + "; " + failure;
            try { Files.writeString(stage.resolve("FAILURE.txt"), note, StandardOpenOption.CREATE_NEW); }
            catch (Exception ignored) { /* original error still names all published files and owned staging */ }
            throw new IllegalStateException(note, failure);
        }
        b.append(",\"verified\":true,\"wroteFile\":true,\"out\":").append(jstr(outPath.toString())).append("}");
        System.out.println(b);
    }

    // ---------- verify-roundtrip (safety self-test; temp write, discarded) ----------
    static void verifyRoundtrip(ESS ess) throws Exception {
        Path dir = Files.createTempDirectory("resaver_rt_");
        Path tmp = dir.resolve("roundtrip.ess");
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"verify-roundtrip\"");
        try {
            ESS.writeESS(ess, tmp, false);
            String diff = verifyWrite(ess, tmp);
            b.append(",\"identical\":").append(diff==null);
            if (diff != null) b.append(",\"divergence\":").append(jstr(diff));
            b.append(",\"changeForms\":").append(ess.getChangeForms().size());
        } finally {
            try { Files.walk(dir).sorted(Comparator.reverseOrder()).forEach(p -> { try { Files.deleteIfExists(p);}catch(Exception e){} }); } catch (Exception e) {}
        }
        b.append("}");
        System.out.println(b);
    }

    // ---------- write ops (ReSaver-delegated, verify-gated) ----------
    static void resetHavokOp(ESS ess, String[] args) throws Exception {
        boolean apply = has(args, "--apply");
        List<String> pos = positionals(args, 2);
        String out = pos.isEmpty() ? null : pos.get(0);
        int[] r = ess.resetHavok(Optional.empty());   // {success, failure}
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"reset-havok\",\"success\":").append(r[0]).append(",\"failure\":").append(r[1]).append(",\"apply\":").append(apply);
        finishWrite(b, ess, apply, out);
    }
    static void cleanseFormListsOp(ESS ess, String[] args) throws Exception {
        boolean apply = has(args, "--apply");
        List<String> pos = positionals(args, 2);
        String out = pos.isEmpty() ? null : pos.get(0);
        int[] r = ess.cleanseFormLists(Optional.empty());  // {entries, forms}
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"cleanse-formlists\",\"nullEntriesRemoved\":").append(r[0]).append(",\"formListsAffected\":").append(r[1]).append(",\"apply\":").append(apply);
        finishWrite(b, ess, apply, out);
    }
    static void removeCreatedOp(ESS ess, String[] args) throws Exception {
        boolean apply = has(args, "--apply");
        List<String> pos = positionals(args, 2);
        String out = pos.isEmpty() ? null : pos.get(0);
        Set<PapyrusElement> removed = ess.removeNonexistentCreated();
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"remove-created\",\"removed\":").append(removed.size()).append(",\"examples\":[");
        int n=0; for (PapyrusElement e : removed) { if (n>=8) break; if (n++>0) b.append(","); b.append(jstr(str(e))); }
        b.append("],\"apply\":").append(apply);
        finishWrite(b, ess, apply, out);
    }

    // ---------- extradata-scan (READ; the unknown-extra-data oracle) ----------
    // NB: getData(false) SWALLOWS the parse error -> null; getData(true) logs only the TOP message,
    // not the type-bearing cause. So we call the body constructor DIRECTLY and walk the cause chain.
    static void extradataScan(ESS ess, String[] args) {
        ESS.ESSContext ctx = ess.getContext();
        String typeArg = flagVal(args, "--type");
        boolean onlyA = "ACHR".equalsIgnoreCase(typeArg), onlyR = "REFR".equalsIgnoreCase(typeArg);
        int limit = flagVal(args,"--limit")!=null ? Integer.parseInt(flagVal(args,"--limit")) : Integer.MAX_VALUE;
        java.util.regex.Pattern P = java.util.regex.Pattern.compile("Unknown ExtraData: type=(\\d+)");
        Map<Integer,Integer> typeCounts = new TreeMap<>();
        Map<Integer,List<String>> examples = new HashMap<>();
        Map<String,Integer> bySrc = new TreeMap<>();
        Map<String,int[]> byCfType = new TreeMap<>();
        Map<String,Integer> otherErr = new TreeMap<>();
        int scanned=0, failed=0;
        for (ChangeForm cf : ess.getChangeForms()) {
            ChangeForm.Type t = cf.getType();
            boolean isA = t==ChangeForm.Type.ACHR, isR = t==ChangeForm.Type.REFR;
            if (!isA && !isR) continue;
            if (onlyA && !isA) continue;
            if (onlyR && !isR) continue;
            if (scanned >= limit) break;
            String ts = str(t);
            byCfType.computeIfAbsent(ts, k->new int[3])[0]++;
            scanned++;
            ByteBuffer body = cf.getBodyData();
            if (body == null) { byCfType.get(ts)[2]++; continue; }
            body.order(ByteOrder.LITTLE_ENDIAN); ((Buffer)body).position(0);
            try {
                if (isA) new ChangeFormACHR(body, cf.getChangeFlags(), cf.getRefID(), Optional.empty(), ctx);
                else     new ChangeFormRefr(body, cf.getChangeFlags(), cf.getRefID(), Optional.empty(), ctx);
                byCfType.get(ts)[2]++;
            } catch (Throwable ex) {
                failed++; byCfType.get(ts)[1]++;
                java.util.regex.Matcher m = P.matcher(chain(ex));
                if (m.find()) {
                    int et = Integer.parseInt(m.group(1));
                    typeCounts.merge(et, 1, Integer::sum);
                    bySrc.merge(ts+":"+et, 1, Integer::sum);
                    List<String> ex5 = examples.computeIfAbsent(et, k->new ArrayList<>());
                    if (ex5.size()<5) ex5.add(refStr(cf.getRefID()));
                } else otherErr.merge(deepest(ex), 1, Integer::sum);
            }
        }
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"extradata-scan\",\"scanned\":").append(scanned).append(",\"failed\":").append(failed);
        b.append(",\"unknownTypes\":[");
        List<Map.Entry<Integer,Integer>> sorted = new ArrayList<>(typeCounts.entrySet());
        sorted.sort((x,y)->Integer.compare(y.getValue(), x.getValue()));
        int n=0; for (Map.Entry<Integer,Integer> e : sorted) {
            if (n++>0) b.append(",");
            b.append("{\"type\":").append(e.getKey()).append(",\"count\":").append(e.getValue()).append(",\"examples\":[");
            List<String> ex5 = examples.get(e.getKey());
            for (int i=0;i<ex5.size();i++){ if(i>0)b.append(","); b.append(jstr(ex5.get(i))); }
            b.append("]}");
        }
        b.append("],\"unknownBySource\":{");
        n=0; for (Map.Entry<String,Integer> e : bySrc.entrySet()){ if(n++>0)b.append(","); b.append(jstr(e.getKey())).append(":").append(e.getValue()); }
        b.append("},\"perType\":{");
        n=0; for (Map.Entry<String,int[]> e : byCfType.entrySet()){ if(n++>0)b.append(","); b.append(jstr(e.getKey())).append(":{\"scanned\":").append(e.getValue()[0]).append(",\"failed\":").append(e.getValue()[1]).append(",\"ok\":").append(e.getValue()[2]).append("}"); }
        b.append("},\"otherFailures\":{");
        n=0; for (Map.Entry<String,Integer> e : otherErr.entrySet()){ if(n>=15)break; if(n++>0)b.append(","); b.append(jstr(e.getKey())).append(":").append(e.getValue()); }
        b.append("}}");
        System.out.println(b);
    }

    // ---------- recon (READ; sync-aware parse-coverage scanner — the standing phantom test) ----------
    // Strict-parses EVERY parseable changeform body type and classifies each failure. For an extra-data
    // "Unknown type N" it records the PREDECESSOR type via reflection on the read-only analysis-overlay's
    // ChangeFormExtraDataData.RECENT list (present only when the overlay is on the classpath) — so a phantom
    // of an upstream stub (one consistent predecessor) is separable from a real type (varied predecessors).
    // Re-run before/after every parse fix to confirm a whole phantom cluster cleared with no regressions.
    static void recon(ESS ess, String[] args) {
        ESS.ESSContext ctx = ess.getContext();
        // Reflection accessor for the overlay's RECENT type-run-up list (graceful if running stock jar).
        List<Integer> recent = null;
        try {
            Field f = ChangeFormExtraDataData.class.getField("RECENT");
            @SuppressWarnings("unchecked") List<Integer> r = (List<Integer>) f.get(null);
            recent = r;
        } catch (Throwable ignore) { /* stock jar: no predecessor histogram */ }

        java.util.regex.Pattern P = java.util.regex.Pattern.compile("Unknown ExtraData: type=(\\d+)");
        Map<String,int[]> byType = new TreeMap<>();                       // cfType -> [ok,fail]
        Map<String,Integer> byCategory = new TreeMap<>();                 // category -> count
        Map<Integer,Integer> unkCount = new TreeMap<>();                  // unknown extra-type -> count
        Map<Integer,TreeMap<Integer,Integer>> unkPred = new TreeMap<>();  // unknown type -> {predecessor -> count}
        Map<String,Integer> nonExtraLoc = new TreeMap<>();                // non-extradata fail location -> count

        for (ChangeForm cf : ess.getChangeForms()) {
            String ty = str(cf.getType());
            boolean parseable = ty.equals("FLST")||ty.equals("LVLN")||ty.equals("LVLI")||ty.equals("REFR")
                ||ty.equals("ACHR")||ty.equals("NPC_")||ty.equals("RELA")||ty.equals("QUST");
            if (!parseable) continue;
            byType.computeIfAbsent(ty, k->new int[2]);
            ByteBuffer body = cf.getBodyData(); if (body == null) continue;
            body.order(ByteOrder.LITTLE_ENDIAN); ((Buffer)body).position(0);
            if (recent != null) recent.clear();
            try {
                switch (ty) {
                    case "FLST": new ChangeFormFLST(body, cf.getChangeFlags(), ctx); break;
                    case "LVLN": case "LVLI": new ChangeFormLeveled(body, cf.getChangeFlags(), ctx); break;
                    case "REFR": new ChangeFormRefr(body, cf.getChangeFlags(), cf.getRefID(), Optional.empty(), ctx); break;
                    case "ACHR": new ChangeFormACHR(body, cf.getChangeFlags(), cf.getRefID(), Optional.empty(), ctx); break;
                    case "NPC_": new ChangeFormNPC(body, cf.getChangeFlags(), ctx); break;
                    case "RELA": new ChangeFormRela(body, cf.getChangeFlags(), cf.getRefID(), ctx); break;
                    case "QUST": new ChangeFormQust(body, cf.getChangeFlags(), ctx); break;
                }
                byType.get(ty)[0]++;
            } catch (Throwable ex) {
                byType.get(ty)[1]++;
                String dm = deepest(ex), path = chain(ex);
                java.util.regex.Matcher um = P.matcher(dm);
                if (um.find()) {
                    int N = Integer.parseInt(um.group(1));
                    byCategory.merge("extra-unknown-type", 1, Integer::sum);
                    unkCount.merge(N, 1, Integer::sum);
                    int pred = (recent != null && recent.size() >= 2) ? recent.get(recent.size()-2) : -1;
                    unkPred.computeIfAbsent(N, k->new TreeMap<>()).merge(pred, 1, Integer::sum);
                } else if (dm.contains("Excessive array count")) {
                    byCategory.merge("excessive-array-count", 1, Integer::sum); nonExtraLoc.merge(locTag(path), 1, Integer::sum);
                } else if (dm.contains("BufferUnderflow")) {
                    byCategory.merge("buffer-underflow", 1, Integer::sum); nonExtraLoc.merge(locTag(path), 1, Integer::sum);
                } else if (dm.contains("Unparsed")) {
                    byCategory.merge("unparsed-tail", 1, Integer::sum); nonExtraLoc.merge(locTag(path), 1, Integer::sum);
                } else {
                    byCategory.merge("other:" + dm.replaceAll("[0-9]+","#"), 1, Integer::sum);
                }
            }
        }

        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"recon\",\"overlayLoaded\":").append(recent != null);
        b.append(",\"perType\":{");
        int n=0; for (Map.Entry<String,int[]> e : byType.entrySet()) { if (n++>0) b.append(",");
            b.append(jstr(e.getKey())).append(":{\"ok\":").append(e.getValue()[0]).append(",\"fail\":").append(e.getValue()[1]).append("}"); }
        b.append("},\"failureCategories\":{");
        n=0; for (Map.Entry<String,Integer> e : sortByValueDesc(byCategory)) { if (n++>0) b.append(",");
            b.append(jstr(e.getKey())).append(":").append(e.getValue()); }
        b.append("},\"unknownTypes\":[");
        List<Map.Entry<Integer,Integer>> us = new ArrayList<>(unkCount.entrySet());
        us.sort((x,y)->Integer.compare(y.getValue(), x.getValue()));
        n=0; for (Map.Entry<Integer,Integer> e : us) { if (n++>0) b.append(","); int N = e.getKey();
            b.append("{\"type\":").append(N).append(",\"count\":").append(e.getValue()).append(",\"preds\":{");
            int m=0; for (Map.Entry<Integer,Integer> p : unkPred.get(N).entrySet()) { if (m++>0) b.append(",");
                b.append("\"").append(p.getKey()).append("\":").append(p.getValue()); }
            b.append("}}"); }
        b.append("],\"nonExtraLoc\":{");
        n=0; for (Map.Entry<String,Integer> e : sortByValueDesc(nonExtraLoc)) { if (n++>0) b.append(",");
            b.append(jstr(e.getKey())).append(":").append(e.getValue()); }
        b.append("}}");
        System.out.println(b);
    }

    // Tag a non-extradata failure by the element/region named in the exception chain.
    static String locTag(String path) {
        for (String k : new String[]{"ANIMATIONS","HAVOK","INVENTORY","EXTRADATA","ExtraData","ChangeFormInventoryItem",
            "BASE_OBJECT","INITIAL","ChangeFormNPC","ChangeFormQust","ChangeFormFLST","Leveled","Rela"})
            if (path.contains(k)) return k;
        return "?";
    }

    static List<Map.Entry<String,Integer>> sortByValueDesc(Map<String,Integer> m) {
        List<Map.Entry<String,Integer>> l = new ArrayList<>(m.entrySet());
        l.sort((x,y)->Integer.compare(y.getValue(), x.getValue()));
        return l;
    }

    // ---------- changeform-diff (READ; localize baked state across two same-playthrough saves) ----------
    static void changeformDiff(ESS essA, String[] args) throws Exception {
        String saveB = arg(args, 2, null);
        if (saveB == null) { err("changeform-diff needs <saveB> [refidHex|--quests]"); return; }
        boolean quests = has(args, "--quests");
        ESS essB = ESS.readESS(callerPath(saveB), new ModelBuilder(new ProgressModel(1))).ESS;
        Map<String,ChangeForm> ia = indexCF(essA), ib = indexCF(essB);
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"changeform-diff\",\"saveB\":").append(jstr(saveB));
        if (quests) {
            b.append(",\"mode\":\"quests\",\"changed\":[");
            int n=0;
            for (Map.Entry<String,ChangeForm> e : ia.entrySet()) {
                ChangeForm a = e.getValue();
                if (a.getType()!=ChangeForm.Type.QUST) continue;
                ChangeForm bb = ib.get(e.getKey());
                if (bb==null) continue;
                if (!Arrays.equals(cfBytes(a), cfBytes(bb))) { if(n++>0) b.append(","); b.append(diffForm(essA,a,essB,bb)); }
            }
            b.append("],\"changedCount\":").append(n).append("}");
        } else {
            String refHex = null;
            for (int i=3;i<args.length;i++){ if(!args[i].startsWith("--")){ refHex=args[i]; break; } }
            if (refHex==null) refHex="00000014";
            Long fid = parseHex(refHex);
            ChangeForm a = pickForm(ia, fid), bb = pickForm(ib, fid);
            b.append(",\"mode\":\"single\",\"refid\":").append(jstr(refHex));
            if (a==null || bb==null) { b.append(",\"found\":false}"); System.out.println(b); return; }
            b.append(",\"found\":true,\"diff\":").append(diffForm(essA,a,essB,bb)).append("}");
        }
        System.out.println(b);
    }
    static ChangeForm pickForm(Map<String,ChangeForm> idx, Long fid) {
        if (fid==null) return null;
        ChangeForm best=null;
        for (ChangeForm c : idx.values())
            if (c.getRefID()!=null && (c.getRefID().FORMID & 0xFFFFFF)==(fid.intValue() & 0xFFFFFF)) {
                if ("ACHR".equalsIgnoreCase(str(c.getType()))) return c;
                if (best==null) best=c;
            }
        return best;
    }
    static String diffForm(ESS essA, ChangeForm a, ESS essB, ChangeForm b) {
        StringBuilder o = new StringBuilder("{");
        o.append("\"refid\":").append(jstr(refStr(a.getRefID()))).append(",\"type\":").append(jstr(str(a.getType())));
        byte[] xa = cfBytes(a), xb = cfBytes(b);
        o.append(",\"lenA\":").append(xa.length).append(",\"lenB\":").append(xb.length);
        int min=Math.min(xa.length,xb.length);
        int pre=0; while (pre<min && xa[pre]==xb[pre]) pre++;
        int suf=0; while (suf<(min-pre) && xa[xa.length-1-suf]==xb[xb.length-1-suf]) suf++;
        boolean identical = (xa.length==xb.length && pre==xa.length);
        o.append(",\"rawIdentical\":").append(identical);
        if (!identical) {
            o.append(",\"firstDivergeOffset\":").append(pre).append(",\"commonSuffix\":").append(suf);
            o.append(",\"windowA\":").append(jstr(hex(xa, pre, Math.min(xa.length, pre+48))));
            o.append(",\"windowB\":").append(jstr(hex(xb, pre, Math.min(xb.length, pre+48))));
            o.append(",\"refIdCandidates\":[").append(refIdCandidates(essB, xb, pre, Math.min(xb.length, pre+24))).append("]");
        }
        o.append(",\"structured\":").append(structuredDiff(flatten(essA,a), flatten(essB,b)));
        // structured INVENTORY diff (item added/removed/count-changed) — inventory parses via typed fields
        Map<String,Long> invA = inventoryMap(essA,a), invB = inventoryMap(essB,b);
        if (!invA.isEmpty() || !invB.isEmpty()) o.append(",\"inventoryDiff\":").append(inventoryDiff(invA, invB));
        o.append("}");
        return o.toString();
    }
    /** itemRefID -> total count. Reads the "inventory" array from getValues() (always holds the
     *  bestEffort partial; the typed INVENTORY field stays null when the array read throws). */
    static Map<String,Long> inventoryMap(ESS ess, ChangeForm cf) {
        Map<String,Long> m = new LinkedHashMap<>();
        try {
            ChangeFormData d = cf.getData(Optional.empty(), ess.getContext(), true);
            if (!(d instanceof GeneralElement)) return m;
            Object inv = null;
            for (Map.Entry<?,?> e : ((GeneralElement)d).getValues().entrySet())
                if (String.valueOf(e.getKey()).equalsIgnoreCase("inventory")) { inv = e.getValue(); break; }
            if (inv instanceof Object[]) for (Object o : (Object[]) inv) if (o instanceof ChangeFormInventoryItem) {
                ChangeFormInventoryItem it = (ChangeFormInventoryItem) o;
                if (it.ITEM != null) m.merge(refStr(it.ITEM), (long) it.COUNT, Long::sum);
            }
        } catch (Throwable ignore) {}
        return m;
    }
    static String inventoryDiff(Map<String,Long> a, Map<String,Long> b) {
        StringBuilder o = new StringBuilder("{\"added\":[");
        int n=0; for (Map.Entry<String,Long> e : b.entrySet()) if (!a.containsKey(e.getKey())) { if(n++>0)o.append(","); o.append("{\"item\":").append(jstr(e.getKey())).append(",\"count\":").append(e.getValue()).append("}"); }
        o.append("],\"removed\":["); n=0; for (Map.Entry<String,Long> e : a.entrySet()) if (!b.containsKey(e.getKey())) { if(n++>0)o.append(","); o.append("{\"item\":").append(jstr(e.getKey())).append(",\"count\":").append(e.getValue()).append("}"); }
        o.append("],\"countChanged\":["); n=0; for (Map.Entry<String,Long> e : a.entrySet()) { Long vb=b.get(e.getKey()); if (vb!=null && !vb.equals(e.getValue())) { if(n++>0)o.append(","); o.append("{\"item\":").append(jstr(e.getKey())).append(",\"a\":").append(e.getValue()).append(",\"b\":").append(vb).append("}"); } }
        o.append("],\"itemsA\":").append(a.size()).append(",\"itemsB\":").append(b.size()).append("}");
        return o.toString();
    }
    /** Resolve candidate RefIDs at the divergence using ReSaver's OWN decoder (read path). Heuristic. */
    static String refIdCandidates(ESS ess, byte[] data, int start, int end) {
        ESS.ESSContext ctx = ess.getContext();
        StringBuilder s = new StringBuilder(); int n=0;
        for (int off=start; off<end && off<data.length; off++) {
            try {
                ByteBuffer bb = ByteBuffer.wrap(data, off, Math.min(8, data.length-off)).order(ByteOrder.LITTLE_ENDIAN);
                RefID r = ctx.readRefID(bb);
                if (r != null && r.PLUGIN != null && r.FORMID != 0) {
                    if (n++>0) s.append(",");
                    s.append("{\"offset\":").append(off).append(",\"ref\":").append(jstr(refStr(r))).append("}");
                    if (n>=8) break;
                }
            } catch (Throwable ignore) {}
        }
        return s.toString();
    }
    static Map<String,String> flatten(ESS ess, ChangeForm cf) {
        Map<String,String> m = new LinkedHashMap<>();
        try {
            ChangeFormData d = cf.getData(Optional.empty(), ess.getContext(), true);
            if (d instanceof GeneralElement) flattenGE("", (GeneralElement)d, m, new int[]{0});
            else if (d != null) m.put("", str(d));
        } catch (Throwable t) { m.put("_parseError", t.toString()); }
        return m;
    }
    static void flattenGE(String prefix, GeneralElement ge, Map<String,String> m, int[] cap) {
        for (Map.Entry<?,?> e : ge.getValues().entrySet()) {
            if (cap[0]++ > 5000) { m.put(prefix+"/_capped","true"); return; }
            String key = prefix + "/" + e.getKey();
            Object v = e.getValue();
            if (v instanceof GeneralElement) flattenGE(key, (GeneralElement)v, m, cap);
            else if (v instanceof Object[]) { Object[] a=(Object[])v; for(int i=0;i<a.length;i++){ if(a[i] instanceof GeneralElement) flattenGE(key+"["+i+"]",(GeneralElement)a[i],m,cap); else m.put(key+"["+i+"]", str(a[i])); } }
            // primitive arrays: stringify by CONTENT (default Object.toString is a per-load identity hash -> false diffs)
            else if (v instanceof byte[])  m.put(key, Arrays.toString((byte[])v));
            else if (v instanceof float[]) m.put(key, Arrays.toString((float[])v));
            else if (v instanceof int[])   m.put(key, Arrays.toString((int[])v));
            else if (v instanceof short[]) m.put(key, Arrays.toString((short[])v));
            else if (v instanceof long[])  m.put(key, Arrays.toString((long[])v));
            else m.put(key, str(v));
        }
    }
    static String structuredDiff(Map<String,String> fa, Map<String,String> fb) {
        // Detection uses FULL values; output is capped + noise-labeled so real signal stands out.
        StringBuilder o = new StringBuilder("{\"changed\":[");
        int n=0, signal=0;
        Set<String> all = new TreeSet<>(); all.addAll(fa.keySet()); all.addAll(fb.keySet());
        for (String k : all) {
            String va=fa.get(k), vb=fb.get(k);
            if (!Objects.equals(va,vb)) {
                boolean noise = isNoisePath(k);
                if (!noise) signal++;
                if (n++>0) o.append(",");
                o.append("{\"path\":").append(jstr(k)).append(",\"noise\":").append(noise)
                 .append(",\"a\":").append(jstr(cap(va))).append(",\"b\":").append(jstr(cap(vb))).append("}");
                if (n>=80) { o.append(",{\"_truncated\":true}"); break; }
            }
        }
        o.append("],\"changedCount\":").append(n).append(",\"signalCount\":").append(signal).append("}");
        return o.toString();
    }
    // Expected churn between adjacent saves (physics/position/unparsed tails) — labeled so a real
    // spell/ability/effect RefID change isn't buried.
    static boolean isNoisePath(String p){ String s=p.toLowerCase(); return s.contains("havok")||s.contains("unparsed_data")||s.contains("unknown_bytes")||s.contains("/initial/pos")||s.contains("/initial/rot")||s.contains("/scale"); }
    static String cap(String s){ if(s==null) return null; return s.length()<=180 ? s : s.substring(0,180)+"…(len "+s.length()+")"; }
    static String hex(byte[] d, int start, int end) {
        StringBuilder s = new StringBuilder();
        for (int i=start; i<end && i<d.length; i++) { s.append(String.format("%02x", d[i]&0xFF)); if (i+1<end && i+1<d.length) s.append(" "); }
        return s.toString();
    }
    static String chain(Throwable ex){ StringBuilder b=new StringBuilder(); for(Throwable t=ex;t!=null;t=t.getCause()) b.append(t.getClass().getSimpleName()).append(": ").append(t.getMessage()).append(" || "); return b.toString(); }
    static String deepest(Throwable ex){ Throwable t=ex; while(t.getCause()!=null) t=t.getCause(); String m=t.getClass().getSimpleName()+": "+t.getMessage(); return m.length()>120?m.substring(0,120):m; }

    // ---------- freeze-report (READ; offline VM-freeze-risk diagnostic) ----------
    // Heuristic known freeze-causing scripts seen on this install (see project_vm_freeze_followup).
    static final String[] KNOWN_FREEZE = {"diversedragon","vddmainsct","_reaper","soulreaper"};

    static void freezeReport(ESS ess, Papyrus pap) {
        int[] undef = pap.countUndefinedElements();
        int unatt = pap.countUnattachedInstances();
        int suspended = pap.getSuspendedStacks().size();
        int funcMsgs = pap.getFunctionMessages().size();
        int unbinds = pap.getUnbinds().size();
        int activeTotal=0, terminated=0, undefinedActive=0;
        Map<String,int[]> activeByScript = new HashMap<>(), suspByScript = new HashMap<>();
        for (ActiveScript a : pap.getActiveScripts().values()) {
            activeTotal++;
            if (a.isTerminated()) terminated++;
            if (a.isUndefined()) undefinedActive++;
            String s = activeScriptName(a);
            if (s != null) activeByScript.computeIfAbsent(s, k->new int[1])[0]++;
        }
        for (SuspendedStack ss : pap.getSuspendedStacks().values()) {
            String s = (ss.getScript()!=null) ? str(ss.getScript().getName()) : null;
            if (s != null) suspByScript.computeIfAbsent(s, k->new int[1])[0]++;
        }
        // cross-ref known freeze-causers across active + suspended stacks
        Map<String,Integer> flagged = new TreeMap<>();
        for (Map<String,int[]> m : Arrays.asList(activeByScript, suspByScript))
            for (Map.Entry<String,int[]> e : m.entrySet()) {
                String ln = e.getKey().toLowerCase();
                for (String k : KNOWN_FREEZE) if (ln.contains(k)) flagged.merge(e.getKey(), e.getValue()[0], Integer::sum);
            }

        // riskLevel reflects actual save PATHOLOGY; known-freeze-mod presence is a separate watchlist
        // (an active known-bad mod is normal — it's only a "monitor this" signal, not an imminent freeze).
        List<String> signals = new ArrayList<>();
        if (undef.length>1 && undef[1]>0) signals.add("undefinedThreads="+undef[1]);
        if (undefinedActive>0) signals.add("undefinedActiveScripts="+undefinedActive);
        if (suspended>50) signals.add("highSuspendedStacks="+suspended);
        else if (suspended>0) signals.add("suspendedStacks="+suspended);
        if (terminated>0) signals.add("terminatedButPresent="+terminated);
        if (unatt>0) signals.add("unattached="+unatt);
        if (!flagged.isEmpty()) signals.add("watchlist:knownFreezeScriptsActive="+flagged.size());
        boolean hi = (undef.length>1 && undef[1]>0) || undefinedActive>0 || suspended>50;
        boolean mod = suspended>0 || (undef.length>0 && undef[0]>20) || funcMsgs>20 || unatt>0 || terminated>0;
        String level = hi ? "HIGH" : mod ? "MODERATE" : "LOW";

        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"freeze-report\",\"riskLevel\":").append(jstr(level));
        b.append(",\"signals\":["); for (int i=0;i<signals.size();i++){ if(i>0)b.append(","); b.append(jstr(signals.get(i))); } b.append("]");
        b.append(",\"counts\":{\"scriptInstances\":").append(pap.getScriptInstances().size())
         .append(",\"activeScripts\":").append(activeTotal).append(",\"terminated\":").append(terminated)
         .append(",\"undefinedActiveScripts\":").append(undefinedActive)
         .append(",\"suspendedStacks\":").append(suspended).append(",\"functionMessages\":").append(funcMsgs)
         .append(",\"unbinds\":").append(unbinds).append(",\"undefinedElements\":").append(undef.length>0?undef[0]:-1)
         .append(",\"undefinedThreads\":").append(undef.length>1?undef[1]:-1).append(",\"unattached\":").append(unatt).append("}");
        b.append(",\"knownFreezeScripts\":{"); int n=0; for (Map.Entry<String,Integer> e : flagged.entrySet()){ if(n++>0)b.append(","); b.append(jstr(e.getKey())).append(":").append(e.getValue()); } b.append("}");
        b.append(",\"topActiveByScript\":["); appendTop(b, activeByScript, 12); b.append("]");
        b.append(",\"topSuspendedByScript\":["); appendTop(b, suspByScript, 12); b.append("]");
        b.append(",\"note\":\"heuristic; known-freeze list seeded from project_vm_freeze_followup; in-game load is the real test\"}");
        System.out.println(b);
    }
    static String activeScriptName(ActiveScript a) {
        try {
            List<StackFrame> frames = a.getStackFrames();
            if (frames != null && !frames.isEmpty()) {
                StackFrame f = frames.get(0);
                if (f.getScriptName() != null) return str(f.getScriptName());
                if (f.getScript() != null) return str(f.getScript().getName());
            }
        } catch (Throwable ignore) {}
        return null;
    }
    static void appendTop(StringBuilder b, Map<String,int[]> m, int n) {
        List<Map.Entry<String,int[]>> l = new ArrayList<>(m.entrySet());
        l.sort((x,y)->Integer.compare(y.getValue()[0], x.getValue()[0]));
        for (int i=0; i<Math.min(n, l.size()); i++) { if(i>0)b.append(","); b.append("{\"script\":").append(jstr(l.get(i).getKey())).append(",\"count\":").append(l.get(i).getValue()[0]).append("}"); }
    }

    // ---------- helpers ----------

    static HasID resolveEID(Papyrus pap, PapyrusContext ctx, String hex) {
        Long v = parseHex(hex);
        if (v == null) return null;
        HasID r = ctx.findAny(ctx.makeEID64(v));
        if (r == null) r = ctx.findAny(ctx.makeEID32(v.intValue()));
        return r;
    }

    static String siJson(ScriptInstance si) { return "{" + siBody(si) + "}"; }
    static String siBody(ScriptInstance si) {
        return "\"id\":" + jstr(str(si.getID()))
             + ",\"script\":" + jstr(si.getScript()==null?null:str(si.getScript().getName()))
             + ",\"refid\":" + jstr(refStr(si.getRefID()))
             + ",\"undefined\":" + si.isUndefined()
             + ",\"unattached\":" + si.isUnattached()
             + ",\"vars\":" + si.getVariables().size();
    }

    static String refStr(RefID r) {
        if (r == null) return null;
        String plugin = (r.PLUGIN != null) ? r.PLUGIN.NAME : "?";
        return plugin + ":" + String.format("%06X", r.FORMID & 0xFFFFFF);
    }

    static Long parseHex(String s) {
        if (s == null) return null;
        String t = s.startsWith("0x") || s.startsWith("0X") ? s.substring(2) : s;
        try { return Long.parseUnsignedLong(t, 16); } catch (Exception e) { return null; }
    }

    static String str(Object o) { return o == null ? null : o.toString(); }
    static String arg(String[] a, int i, String def) { return a.length > i ? a[i] : def; }
    static boolean has(String[] a, String flag) { for (String s : a) if (s.equals(flag)) return true; return false; }
    static String flagVal(String[] a, String flag) { for (int i=0;i<a.length-1;i++) if (a[i].equals(flag)) return a[i+1]; return null; }
    static List<String> positionals(String[] a, int from) {
        List<String> p = new ArrayList<>();
        for (int i = from; i < a.length; i++) {
            if (a[i].startsWith("--")) { if (a[i].equals("--type") || a[i].equals("--limit") || a[i].equals("--script")) i++; continue; }
            p.add(a[i]);
        }
        return p;
    }
    // ---------- globaldata (lens 2): reflection-read the 3 Global Data tables; diff by type ----------
    // ReSaver keeps TABLE1/2/3 (List<GlobalData>) PRIVATE and most blocks as raw DefaultGlobalDataBlock.
    // We read type + serialized block bytes (read-only) → diff between saves. Type 100=ProcessLists is the
    // prime suspect for an equip-gated active-effect/process trigger the changeform doesn't hold.
    @SuppressWarnings("unchecked")
    static List<GlobalData> gdTable(ESS ess, String field) {
        try { java.lang.reflect.Field f = ESS.class.getDeclaredField(field); f.setAccessible(true);
               Object v = f.get(ess); return (v instanceof List) ? (List<GlobalData>) v : null; }
        catch (Throwable t) { return null; }
    }
    static List<GlobalData> gdBlocks(ESS ess) {
        List<GlobalData> all = new ArrayList<>();
        for (String f : new String[]{"TABLE1","TABLE2","TABLE3"}) { List<GlobalData> t = gdTable(ess, f); if (t != null) all.addAll(t); }
        return all;
    }
    static int gdSize(GlobalData gd) { try { GlobalDataBlock blk = gd.getDataBlock(); return blk != null ? blk.calculateSize() : gd.calculateSize(); } catch (Throwable t) { return -1; } }
    // serialized block content; skip the heavy Papyrus block (1001) — size-only there.
    static byte[] gdBytes(GlobalData gd) {
        if (gd.getType() == 1001) return null;
        try { GlobalDataBlock blk = gd.getDataBlock(); if (blk == null) return null;
              int sz = blk.calculateSize(); if (sz < 0 || sz > 64*1024*1024) return null;
              ByteBuffer buf = ByteBuffer.allocate(sz).order(ByteOrder.LITTLE_ENDIAN); blk.write(buf); return buf.array(); }
        catch (Throwable t) { return null; }
    }
    static String gdSha(byte[] bytes) {
        if (bytes == null) return null;
        try { byte[] h = java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
              StringBuilder s = new StringBuilder(); for (int i=0;i<10 && i<h.length;i++) s.append(String.format("%02x", h[i])); return s.toString(); }
        catch (Throwable t) { return null; }
    }
    static String gdName(int t) {
        switch (t) {
            case 0: return "MiscStats"; case 1: return "PlayerLocation"; case 2: return "TES";
            case 3: return "GlobalVariables"; case 4: return "CreatedObjects"; case 5: return "Effects(IMOD)";
            case 6: return "WeatherSystem"; case 7: return "AudioData"; case 8: return "SkyCells";
            case 100: return "ProcessLists"; case 101: return "Combat"; case 102: return "Interface";
            case 103: return "ActorCauses"; case 104: return "Unknown104"; case 105: return "DetectionManager";
            case 106: return "LocationMetaData"; case 107: return "QuestStaticData"; case 108: return "StoryTeller";
            case 109: return "MagicFavorites"; case 110: return "PlayerControls"; case 111: return "StoryEventManager";
            case 112: return "IngredientShared"; case 113: return "MenuControls"; case 114: return "MenuTopicManager";
            case 1001: return "Papyrus"; case 1002: return "AnimObjects"; case 1003: return "Timer";
            case 1004: return "SynchronizedAnimations"; case 1005: return "Main"; default: return "Type" + t;
        }
    }
    static void globalDataOp(ESS ess) {
        List<GlobalData> all = gdBlocks(ess);
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"globaldata\",\"reflectionOk\":").append(!all.isEmpty()).append(",\"blocks\":[");
        int n = 0;
        for (GlobalData gd : all) { int type = gd.getType();
            if (n++ > 0) b.append(",");
            b.append("{\"type\":").append(type).append(",\"name\":").append(jstr(gdName(type)))
             .append(",\"size\":").append(gdSize(gd)).append(",\"sha\":").append(jstr(gdSha(gdBytes(gd)))).append("}");
        }
        b.append("],\"count\":").append(n).append("}");
        System.out.println(b);
    }
    static void globalDataDiff(ESS essA, String[] args) throws Exception {
        String saveB = arg(args, 2, null);
        if (saveB == null) { err("globaldata-diff needs <saveB>"); return; }
        ESS essB = ESS.readESS(callerPath(saveB), new ModelBuilder(new ProgressModel(1))).ESS;
        Map<Integer,byte[]> ma = new LinkedHashMap<>(), mb = new LinkedHashMap<>();
        Map<Integer,Integer> sa = new LinkedHashMap<>(), sb = new LinkedHashMap<>();
        for (GlobalData gd : gdBlocks(essA)) { ma.put(gd.getType(), gdBytes(gd)); sa.put(gd.getType(), gdSize(gd)); }
        for (GlobalData gd : gdBlocks(essB)) { mb.put(gd.getType(), gdBytes(gd)); sb.put(gd.getType(), gdSize(gd)); }
        StringBuilder b = new StringBuilder();
        b.append("{\"ok\":true,\"op\":\"globaldata-diff\",\"saveB\":").append(jstr(saveB))
         .append(",\"reflectionOk\":").append(!ma.isEmpty() && !mb.isEmpty()).append(",\"changed\":[");
        int n = 0;
        Set<Integer> types = new TreeSet<>(); types.addAll(ma.keySet()); types.addAll(mb.keySet());
        for (int type : types) {
            boolean inA = ma.containsKey(type), inB = mb.containsKey(type);
            byte[] xa = ma.get(type), xb = mb.get(type);
            boolean changed = (!inA || !inB) ? true
                : (xa == null || xb == null) ? !Objects.equals(sa.get(type), sb.get(type))
                : !Arrays.equals(xa, xb);
            if (!changed) continue;
            if (n++ > 0) b.append(",");
            b.append("{\"type\":").append(type).append(",\"name\":").append(jstr(gdName(type)))
             .append(",\"inA\":").append(inA).append(",\"inB\":").append(inB)
             .append(",\"sizeA\":").append(sa.getOrDefault(type,-1)).append(",\"sizeB\":").append(sb.getOrDefault(type,-1));
            if (inA && inB && xa != null && xb != null) {
                int min = Math.min(xa.length, xb.length), pre = 0; while (pre < min && xa[pre] == xb[pre]) pre++;
                b.append(",\"firstDivergeOffset\":").append(pre)
                 .append(",\"windowA\":").append(jstr(hex(xa, pre, Math.min(xa.length, pre+48))))
                 .append(",\"windowB\":").append(jstr(hex(xb, pre, Math.min(xb.length, pre+48))))
                 .append(",\"refIdCandidates\":[").append(refIdCandidates(essB, xb, pre, Math.min(xb.length, pre+24))).append("]");
            }
            b.append("}");
        }
        b.append("],\"changedCount\":").append(n).append("}");
        System.out.println(b);
    }

    static void err(String m) { throw new IllegalArgumentException(m); }
    static void kv(StringBuilder b, String k, String v) { b.append(",\"").append(k).append("\":").append(jstr(v)); }
    static void kvn(StringBuilder b, String k, long v) { b.append(",\"").append(k).append("\":").append(v); }

    static String jstr(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default: if (c < 0x20) b.append(String.format("\\u%04x", (int)c)); else b.append(c);
            }
        }
        return b.append("\"").toString();
    }
}
