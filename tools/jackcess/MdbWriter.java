// MdbWriter — create a real Access (JET4) .mdb from a directory of typed TSVs.
//
// Usage:  java -cp "tools/jackcess/*" MdbWriter.java <specdir> <out.mdb>
// (single-file source launch — no javac needed; requires the jdk.compiler
//  module, present in the stock OpenJDK packages)
//
// Spec: every "<table>.tsv" in <specdir> becomes a table. The first line is
// the header: TAB-separated "name:type" pairs, type in
// {text, long, double, memo, blobfile}. Data lines are TAB-separated values;
// empty cell = NULL. For "blobfile" columns the cell holds a PATH whose bytes
// are stored in an OLE/binary column (used for the ITU `masks.mask` zipped
// XML payload).
//
// Part of the SHARC-Orbit manual-system pair generator (SRS + Mask MDB): the
// output is readable by access_parser / mdbtools like any ITU filing.
import java.io.File;
import java.nio.file.*;
import java.util.*;
import com.healthmarketscience.jackcess.*;

public class MdbWriter {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: MdbWriter <specdir> <out.mdb>");
            System.exit(2);
        }
        File specDir = new File(args[0]);
        File out = new File(args[1]);
        if (out.exists()) out.delete();

        Database db = new DatabaseBuilder(out)
                .setFileFormat(Database.FileFormat.V2000)  // JET4 .mdb
                .create();
        try {
            File[] tsvs = specDir.listFiles((d, n) -> n.endsWith(".tsv"));
            if (tsvs == null) tsvs = new File[0];
            Arrays.sort(tsvs);
            for (File tsv : tsvs) {
                String tableName = tsv.getName().replaceFirst("\\.tsv$", "");
                List<String> lines = Files.readAllLines(tsv.toPath());
                if (lines.isEmpty()) continue;

                String[] header = lines.get(0).split("\t", -1);
                String[] names = new String[header.length];
                String[] types = new String[header.length];
                TableBuilder tb = new TableBuilder(tableName);
                for (int c = 0; c < header.length; c++) {
                    String[] parts = header[c].split(":", 2);
                    names[c] = parts[0];
                    types[c] = parts.length > 1 ? parts[1] : "text";
                    DataType dt;
                    switch (types[c]) {
                        case "long":     dt = DataType.LONG; break;
                        case "double":   dt = DataType.DOUBLE; break;
                        case "memo":     dt = DataType.MEMO; break;
                        case "blobfile": dt = DataType.OLE; break;
                        default:         dt = DataType.TEXT; break;
                    }
                    tb.addColumn(new ColumnBuilder(names[c], dt));
                }
                Table table = tb.toTable(db);

                for (int r = 1; r < lines.size(); r++) {
                    if (lines.get(r).isEmpty()) continue;
                    String[] cells = lines.get(r).split("\t", -1);
                    Object[] row = new Object[names.length];
                    for (int c = 0; c < names.length && c < cells.length; c++) {
                        String v = cells[c];
                        if (v.isEmpty()) { row[c] = null; continue; }
                        switch (types[c]) {
                            case "long":     row[c] = Long.parseLong(v); break;
                            case "double":   row[c] = Double.parseDouble(v); break;
                            case "blobfile": row[c] = Files.readAllBytes(Paths.get(v)); break;
                            default:         row[c] = v; break;
                        }
                    }
                    table.addRow(row);
                }
                System.out.println("table " + tableName + ": "
                        + (lines.size() - 1) + " row(s)");
            }
        } finally {
            db.close();
        }
        System.out.println("written: " + out.getAbsolutePath());
    }
}
