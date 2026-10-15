/** Data-free native qualification helper. Run only against isolated private fixture copies. */
import java.nio.file.*;
import java.lang.reflect.Field;
import java.util.*;
import resaver.ess.*;
import resaver.ess.papyrus.*;
import resaver.ProgressModel;

public final class NativeSafetyProbe {
    public static void main(String[] args) throws Exception {
        Path input = Paths.get(args[1]), output = Paths.get(args[2]);
        ESS ess = ESS.readESS(input, new ModelBuilder(new ProgressModel(1))).ESS;
        if (args[0].equals("inject-flst")) {
            ResaverCLI.requireNewPair(output);
            for (ChangeForm form : ess.getChangeForms()) {
                if (form.getType() != ChangeForm.Type.FLST) continue;
                ChangeFormData parsed = form.getData(Optional.empty(), ess.getContext(), false);
                if (!(parsed instanceof ChangeFormFLST)) continue;
                Field field = ChangeFormFLST.class.getDeclaredField("FORMS");
                field.setAccessible(true);
                @SuppressWarnings("unchecked")
                List<RefID> forms = (List<RefID>) field.get(parsed);
                if (forms == null) continue;
                forms.add(ess.getContext().makeRefID(0));
                if (!((ChangeFormFLST) parsed).containsNullrefs() || !form.updateRawData(parsed))
                    throw new IllegalStateException("fixture null-reference injection did not take effect");
                ESS.writeESS(ess, output, false);
                ESS loaded = ESS.readESS(output, new ModelBuilder(new ProgressModel(1))).ESS;
                ChangeForm reread = loaded.getChangeForms().stream()
                        .filter(f -> f.getRefID().equals(form.getRefID())).findFirst().orElseThrow();
                if (!((ChangeFormFLST) reread.getData(Optional.empty(), loaded.getContext(), false)).containsNullrefs())
                    throw new IllegalStateException("native fixture readback lost the injected null reference");
                System.out.println("{\"ok\":true,\"injectedNullRefs\":1}");
                return;
            }
            throw new IllegalStateException("fixture contains no writable FLST body");
        }
        if (args[0].equals("global-corruption-control")) {
            byte[] before = ResaverCLI.fileDigest(input);
            GlobalVariable global = ess.getGlobals().getVariables().iterator().next();
            global.setValue(global.getValue() + 1);
            String divergence = ResaverCLI.verifyWrite(ess, input);
            if (divergence == null || !Arrays.equals(before, ResaverCLI.fileDigest(input)))
                throw new IllegalStateException("global corruption was accepted or the input was changed/deleted");
            System.out.println("{\"ok\":true,\"corruptionRefused\":true,\"inputPreserved\":true}");
            return;
        }
        if (args[0].equals("papyrus-corruption-control")) {
            byte[] before = ResaverCLI.fileDigest(input);
            for (ScriptInstance instance : ess.getPapyrus().getScriptInstances().values()) {
                List<Variable> variables = instance.getVariables();
                for (int index = 1; index < variables.size(); index++) {
                    if (!(variables.get(index) instanceof Variable.Int)) continue;
                    instance.setVariable(index, new Variable.Int(87654321));
                    String divergence = ResaverCLI.verifyWrite(ess, input);
                    if (divergence == null || !Arrays.equals(before, ResaverCLI.fileDigest(input)))
                        throw new IllegalStateException("Papyrus value corruption was accepted or input changed/deleted");
                    System.out.println("{\"ok\":true,\"corruptionRefused\":true,\"inputPreserved\":true}");
                    return;
                }
            }
            throw new IllegalStateException("fixture contains no writable integer variable");
        }
        throw new IllegalArgumentException("unknown qualification operation");
    }
}
