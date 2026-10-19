# Built-in omics example matrices

These files are compact, reproducible teaching subsets of public datasets. They
are installed into a user's project only after the user clicks **载入案例**.
They are not synthetic, and they must not be presented as the complete source
study or as suitable for publication-grade reanalysis.

| File | Public source | Compacting rule |
| --- | --- | --- |
| `bulk_airway_gse52778.csv` | Bioconductor `airway`, GEO GSE52778 | Raw counts; 1,500 genes with the highest mean CPM after requiring >=10 counts in >=4 samples, plus CRISPLD2 if needed; all 8 samples. |
| `single_cell_kang_gse96583.csv` | Kang et al. 2018, GEO GSE96583 | Raw UMI counts; deterministic first 20 singlet CD14+ monocytes from each of four donors in each ctrl/stim condition; a fixed interferon/QC/monocyte gene panel. |
| `proteomics_ubilength_ctrl_ubi4.csv` | Bioconductor DEP `UbiLength`, Zhang et al. 2017 | MaxQuant LFQ intensities; contaminants/reverse/site-only rows removed; Ctrl and Ubi4 triplicates. |
| `metabolomics_human_cachexia.csv` | MetaboAnalyst `human_cachexia.csv`, Eisner et al. 2010 | Original concentrations; all 63 metabolites; first 10 cachectic and first 10 control samples in source order. |

The public accession, article/source URL, compacting rule, and the SHA-256 of
the installed matrix are also persisted in the created asset metadata. FDR in a
compact example applies only to the included feature universe.
