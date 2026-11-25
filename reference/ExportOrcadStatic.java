// Exports address-bound evidence from an existing analyzed Ghidra program.
// @category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.block.BasicBlockModel;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.regex.Pattern;

public class ExportOrcadStatic extends GhidraScript {
    private static final Pattern FEATURE = Pattern.compile(
        "(?i)psp_eng\\.dll|psp_wobj\\.dll|netlist|convergen|transient analysis|" +
        "simulation profile|runtime settings|plot window|waveform|simulation complete|" +
        "trapezoidal|newton|matrix pivot|\\.SUBCKT|\\.TRAN");

    private PrintWriter output(Path directory, String name) throws Exception {
        return new PrintWriter(Files.newBufferedWriter(
            directory.resolve(name), StandardCharsets.UTF_8));
    }

    private String field(Object value) {
        return String.valueOf(value).replace("\t", "\\t").replace("\r", "\\r")
            .replace("\n", "\\n");
    }

    @Override
    public void run() throws Exception {
        Path dir = Files.createDirectories(Path.of(getScriptArgs()[0], currentProgram.getName()));
        var listing = currentProgram.getListing();
        var symbols = currentProgram.getSymbolTable();
        var functions = currentProgram.getFunctionManager();
        var selected = new LinkedHashSet<Function>();
        try (var metadata = output(dir, "metadata.txt");
             var inventory = output(dir, "functions.tsv");
             var strings = output(dir, "strings.tsv");
             var xrefs = output(dir, "feature-xrefs.tsv");
             var imports = output(dir, "externals.tsv")) {
            metadata.println("sha256=" + currentProgram.getExecutableSHA256());
            metadata.println("format=" + currentProgram.getExecutableFormat());
            metadata.println("language=" + currentProgram.getLanguageID());
            metadata.println("imageBase=" + currentProgram.getImageBase());

            inventory.println("entry\tname\tbody_addresses\tthunk");
            for (var f : functions.getFunctions(true)) {
                inventory.printf("%s\t%s\t%d\t%b%n", f.getEntryPoint(), field(f.getName(true)),
                    f.getBody().getNumAddresses(), f.isThunk());
                if (f.getName().matches("entry|.*PSpice(DoAnalysis|Pause|DotCommand|BuildLibIndex|ParamValidate|SetCallback).*")
                    || (currentProgram.getName().equals("psp_eng.dll")
                        && f.getEntryPoint().toString().matches("100c(74a0|7880|89e0|7260|81a0|71f0)"))) {
                    selected.add(f);
                }
            }
            imports.println("namespace\tname\taddress");
            var external = symbols.getExternalSymbols();
            while (external.hasNext()) {
                var symbol = external.next();
                imports.printf("%s\t%s\t%s%n", field(symbol.getParentNamespace().getName()),
                    field(symbol.getName()), symbol.getAddress());
            }
            xrefs.println("data_address\tvalue\tsource\tref_type\tfunction_entry");
            strings.println("address\tvalue");
            var data = listing.getDefinedData(true);
            while (data.hasNext() && !monitor.isCancelled()) {
                var item = data.next();
                if (!(item.getValue() instanceof String value)) {
                    continue;
                }
                strings.printf("%s\t%s%n", item.getAddress(), field(value));
                if (!FEATURE.matcher(value).find()) {
                    continue;
                }
                for (Reference ref : getReferencesTo(item.getAddress())) {
                    var f = functions.getFunctionContaining(ref.getFromAddress());
                    xrefs.printf("%s\t%s\t%s\t%s\t%s%n", item.getAddress(), field(value),
                        ref.getFromAddress(), ref.getReferenceType(), f == null ? "" : f.getEntryPoint());
                    if (f != null && selected.size() < 24) {
                        selected.add(f);
                    }
                }
            }
        }
        var decompiler = new DecompInterface();
        var blocks = new BasicBlockModel(currentProgram);
        try (var cfg = output(dir, "cfg.tsv"); var pseudo = output(dir, "pseudocode.txt")) {
            cfg.println("function_entry\tblock\ttarget\tflow");
            decompiler.openProgram(currentProgram);
            for (var f : selected) {
                if (monitor.isCancelled()) {
                    break;
                }
                var result = decompiler.decompileFunction(f, 12, monitor);
                pseudo.printf("%n%s %s%n", f.getEntryPoint(), f.getName(true));
                var code = result.getDecompiledFunction();
                pseudo.println(code == null ? result.getErrorMessage() : code.getC());
                var iterator = blocks.getCodeBlocksContaining(f.getBody(), monitor);
                while (iterator.hasNext()) {
                    var block = iterator.next();
                    var destinations = block.getDestinations(monitor);
                    while (destinations.hasNext()) {
                        var edge = destinations.next();
                        cfg.printf("%s\t%s\t%s\t%s%n", f.getEntryPoint(),
                            block.getFirstStartAddress(), edge.getDestinationAddress(), edge.getFlowType());
                    }
                }
            }
        } finally {
            decompiler.dispose();
        }
    }
}
