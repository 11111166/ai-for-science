#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(jsonlite)
  library(DESeq2)
  library(edgeR)
  library(clusterProfiler)
  library(AnnotationDbi)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: omics_advanced.R INPUT_TABLE METADATA_JSON OUTPUT_JSON")
input_path <- args[[1]]
metadata <- fromJSON(args[[2]], simplifyVector = TRUE)
output_path <- args[[3]]

required <- c("DESeq2", "edgeR", "clusterProfiler", "jsonlite", "AnnotationDbi")
missing_packages <- required[!vapply(required, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
if (length(missing_packages)) stop(paste("missing R packages:", paste(missing_packages, collapse = ", ")))

delimiter <- if (grepl("\\.(tsv|txt)$", input_path, ignore.case = TRUE)) "\t" else ","
table <- read.table(input_path, header = TRUE, sep = delimiter, check.names = FALSE, quote = "\"", comment.char = "", stringsAsFactors = FALSE)
samples <- c(metadata$reference_samples, metadata$comparison_samples)
if (!all(samples %in% colnames(table))) stop("selected samples are missing from the matrix")
if (nrow(table) < 2 || nrow(table) > 10000) stop("advanced analysis requires 2-10,000 features")
if (length(samples) < 4 || length(samples) > 24) stop("advanced analysis requires 4-24 selected samples")

features <- trimws(as.character(table[[1]]))
features[!nzchar(features)] <- paste0("feature_", which(!nzchar(features)))
counts_numeric <- as.matrix(table[, samples, drop = FALSE])
storage.mode(counts_numeric) <- "double"
if (any(!is.finite(counts_numeric)) || any(counts_numeric < 0)) stop("DESeq2/edgeR require complete non-negative counts")
if (any(abs(counts_numeric - round(counts_numeric)) > 1e-8)) stop("DESeq2/edgeR require raw integer counts")
counts <- round(counts_numeric)
rownames(counts) <- make.unique(features)
keep <- rowSums(counts) >= 10
counts <- counts[keep, , drop = FALSE]
kept_features <- features[keep]
if (nrow(counts) < 10) stop("fewer than 10 genes remain after count filtering")

condition <- factor(c(rep("reference", length(metadata$reference_samples)), rep("comparison", length(metadata$comparison_samples))), levels = c("reference", "comparison"))
coldata <- data.frame(condition = condition, row.names = samples)
fdr <- as.numeric(metadata$fdr_threshold)
effect_threshold <- as.numeric(metadata$effect_threshold)

clean_frame <- function(frame, columns, limit = 100L) {
  if (is.null(frame) || !nrow(frame)) return(list())
  frame <- frame[, intersect(columns, colnames(frame)), drop = FALSE]
  frame <- head(frame, limit)
  # jsonlite serializes one-row data.frames as singleton arrays, producing
  # [[{"feature": ...}]] instead of the record array expected by the UI.
  lapply(seq_len(nrow(frame)), function(index) as.list(frame[index, , drop = FALSE]))
}

dds <- DESeqDataSetFromMatrix(countData = counts, colData = coldata, design = ~ condition)
dds <- DESeq(dds, quiet = TRUE)
deseq_raw <- as.data.frame(results(dds, contrast = c("condition", "comparison", "reference"), independentFiltering = TRUE))
deseq_raw$feature <- kept_features
deseq_raw <- deseq_raw[order(deseq_raw$padj, -abs(deseq_raw$log2FoldChange), na.last = TRUE), , drop = FALSE]
deseq_sig <- !is.na(deseq_raw$padj) & deseq_raw$padj <= fdr & abs(deseq_raw$log2FoldChange) >= effect_threshold
deseq_rows <- data.frame(
  feature = deseq_raw$feature,
  base_mean = deseq_raw$baseMean,
  log2_fold_change = deseq_raw$log2FoldChange,
  lfc_se = deseq_raw$lfcSE,
  statistic = deseq_raw$stat,
  p_value = deseq_raw$pvalue,
  adjusted_p_value = deseq_raw$padj,
  stringsAsFactors = FALSE
)

y <- DGEList(counts = counts, group = condition, genes = data.frame(feature = kept_features, stringsAsFactors = FALSE))
keep_edge <- filterByExpr(y, group = condition)
y <- y[keep_edge, , keep.lib.sizes = FALSE]
y <- calcNormFactors(y, method = "TMM")
design <- model.matrix(~ condition)
y <- estimateDisp(y, design)
fit <- glmQLFit(y, design, robust = TRUE)
qlf <- glmQLFTest(fit, coef = ncol(design))
edge_raw <- topTags(qlf, n = Inf, sort.by = "PValue")$table
edge_rows <- data.frame(
  feature = edge_raw$feature,
  log2_fold_change = edge_raw$logFC,
  log_cpm = edge_raw$logCPM,
  f_statistic = edge_raw$F,
  p_value = edge_raw$PValue,
  adjusted_p_value = edge_raw$FDR,
  stringsAsFactors = FALSE
)
edge_sig <- edge_rows$adjusted_p_value <= fdr & abs(edge_rows$log2_fold_change) >= effect_threshold

consensus <- merge(
  deseq_rows[, c("feature", "log2_fold_change", "adjusted_p_value")],
  edge_rows[, c("feature", "log2_fold_change", "adjusted_p_value")],
  by = "feature", suffixes = c("_deseq2", "_edger")
)
consensus$direction_agrees <- sign(consensus$log2_fold_change_deseq2) == sign(consensus$log2_fold_change_edger)
consensus$significant_both <- consensus$direction_agrees & consensus$adjusted_p_value_deseq2 <= fdr & consensus$adjusted_p_value_edger <= fdr & abs(consensus$log2_fold_change_deseq2) >= effect_threshold & abs(consensus$log2_fold_change_edger) >= effect_threshold
consensus <- consensus[order(!consensus$significant_both, pmax(consensus$adjusted_p_value_deseq2, consensus$adjusted_p_value_edger), -abs(consensus$log2_fold_change_deseq2)), , drop = FALSE]

species <- as.character(metadata$species)
id_type <- as.character(metadata$gene_id_type)
org_package <- if (species == "mouse") "org.Mm.eg.db" else "org.Hs.eg.db"
kegg_code <- if (species == "mouse") "mmu" else "hsa"
if (!requireNamespace(org_package, quietly = TRUE)) stop(paste("missing annotation package", org_package))
orgdb <- getExportedValue(org_package, org_package)

extract_ids <- function(values, kind) {
  vapply(values, function(value) {
    tokens <- strsplit(as.character(value), "|", fixed = TRUE)[[1]]
    tokens <- trimws(tokens)
    if (kind == "ensembl") {
      pattern <- if (species == "mouse") "^ENSMUSG[0-9]+" else "^ENSG[0-9]+"
      hit <- tokens[grepl(pattern, tokens)]
      if (length(hit)) return(sub("\\..*$", "", hit[[1]]))
    }
    if (kind == "entrez") {
      hit <- tokens[grepl("^[0-9]+$", tokens)]
      if (length(hit)) return(hit[[1]])
    }
    tokens[[1]]
  }, character(1))
}

if (id_type == "auto") {
  if (mean(grepl(if (species == "mouse") "ENSMUSG[0-9]+" else "ENSG[0-9]+", kept_features)) >= 0.5) id_type <- "ensembl"
  else if (mean(grepl("^[0-9]+(?:\\||$)", kept_features)) >= 0.8) id_type <- "entrez"
  else id_type <- "symbol"
}
keytype <- c(symbol = "SYMBOL", ensembl = "ENSEMBL", entrez = "ENTREZID")[[id_type]]
source_ids <- extract_ids(deseq_raw$feature, id_type)
mapped_entrez <- suppressMessages(mapIds(orgdb, keys = unique(source_ids), keytype = keytype, column = "ENTREZID", multiVals = "first"))
entrez_by_row <- unname(mapped_entrez[source_ids])
mapping_ok <- !is.na(entrez_by_row) & nzchar(entrez_by_row)

warnings <- character()
safe_rows <- function(expr, columns, method_name, limit = 20L) {
  tryCatch({
    value <- suppressMessages(suppressWarnings(expr))
    if (is.null(value)) return(list())
    frame <- as.data.frame(value)
    clean_frame(frame, columns, limit)
  }, error = function(error) {
    warnings <<- c(warnings, paste0(method_name, "：", conditionMessage(error)))
    list()
  })
}

universe <- unique(entrez_by_row[mapping_ok])
sig_up <- unique(entrez_by_row[mapping_ok & deseq_sig & deseq_raw$log2FoldChange > 0])
sig_down <- unique(entrez_by_row[mapping_ok & deseq_sig & deseq_raw$log2FoldChange < 0])
ora_columns <- c("ID", "Description", "ONTOLOGY", "GeneRatio", "BgRatio", "pvalue", "p.adjust", "qvalue", "geneID", "Count")

script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_directory <- if (length(script_argument)) dirname(normalizePath(sub("^--file=", "", script_argument[[1]]))) else getwd()
default_kegg_cache <- normalizePath(file.path(script_directory, "..", "data", "kegg_cache_v1"), mustWork = FALSE)
kegg_cache <- Sys.getenv("OMICS_KEGG_CACHE", unset = default_kegg_cache)
kegg_gene_file <- file.path(kegg_cache, paste0(kegg_code, "_term_gene.tsv"))
kegg_name_file <- file.path(kegg_cache, paste0(kegg_code, "_term_name.tsv"))
kegg_cached <- file.exists(kegg_gene_file) && file.exists(kegg_name_file)
if (kegg_cached) {
  kegg_term_gene <- read.delim(kegg_gene_file, colClasses = "character", check.names = FALSE)
  kegg_term_name <- read.delim(kegg_name_file, colClasses = "character", check.names = FALSE)
  kegg_source <- "versioned_KEGG_REST_cache"
} else {
  kegg_source <- "live_KEGG_REST"
}

go_rows <- list()
for (direction in c("up", "down")) {
  genes <- if (direction == "up") sig_up else sig_down
  rows <- if (length(genes) >= 3) safe_rows(enrichGO(gene = genes, universe = universe, OrgDb = orgdb, keyType = "ENTREZID", ont = "ALL", pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1, readable = TRUE), ora_columns, paste("GO", direction)) else list()
  if (length(rows)) for (index in seq_along(rows)) rows[[index]]$direction <- direction
  go_rows <- c(go_rows, rows)
}
kegg_rows <- list()
for (direction in c("up", "down")) {
  genes <- if (direction == "up") sig_up else sig_down
  rows <- if (length(genes) >= 3) {
    if (kegg_cached) safe_rows(enricher(gene = genes, universe = universe, TERM2GENE = kegg_term_gene, TERM2NAME = kegg_term_name, pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1), ora_columns, paste("KEGG", direction))
    else safe_rows(enrichKEGG(gene = genes, universe = universe, organism = kegg_code, keyType = "ncbi-geneid", pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1), ora_columns, paste("KEGG", direction))
  } else list()
  if (length(rows)) for (index in seq_along(rows)) rows[[index]]$direction <- direction
  kegg_rows <- c(kegg_rows, rows)
}

rank_frame <- data.frame(entrez = entrez_by_row, statistic = deseq_raw$stat)
rank_frame <- rank_frame[!is.na(rank_frame$entrez) & is.finite(rank_frame$statistic), , drop = FALSE]
rank_frame <- rank_frame[order(-abs(rank_frame$statistic)), , drop = FALSE]
rank_frame <- rank_frame[!duplicated(rank_frame$entrez), , drop = FALSE]
gene_rank <- rank_frame$statistic
names(gene_rank) <- rank_frame$entrez
gene_rank <- sort(gene_rank, decreasing = TRUE)
gsea_columns <- c("ID", "Description", "setSize", "enrichmentScore", "NES", "pvalue", "p.adjust", "qvalue", "rank", "leading_edge", "core_enrichment")
gsea_go <- if (length(gene_rank) >= 20) safe_rows(gseGO(geneList = gene_rank, OrgDb = orgdb, keyType = "ENTREZID", ont = "ALL", minGSSize = 5, maxGSSize = 500, pvalueCutoff = 1, pAdjustMethod = "BH", by = "fgsea", verbose = FALSE), gsea_columns, "GO GSEA") else list()
gsea_kegg <- if (length(gene_rank) >= 20) {
  if (kegg_cached) safe_rows(GSEA(geneList = gene_rank, TERM2GENE = kegg_term_gene, TERM2NAME = kegg_term_name, minGSSize = 5, maxGSSize = 500, pvalueCutoff = 1, pAdjustMethod = "BH", by = "fgsea", verbose = FALSE), gsea_columns, "KEGG GSEA")
  else safe_rows(gseKEGG(geneList = gene_rank, organism = kegg_code, keyType = "ncbi-geneid", minGSSize = 5, maxGSSize = 500, pvalueCutoff = 1, pAdjustMethod = "BH", by = "fgsea", verbose = FALSE), gsea_columns, "KEGG GSEA")
} else list()

payload <- list(
  status = "completed",
  input = list(species = species, requested_gene_id_type = as.character(metadata$gene_id_type), detected_gene_id_type = id_type, input_feature_count = nrow(table), filtered_feature_count = nrow(counts), sample_count = length(samples)),
  deseq2 = list(method = "DESeq2 negative-binomial Wald test with median-of-ratios size factors", version = as.character(packageVersion("DESeq2")), significant_count = sum(deseq_sig, na.rm = TRUE), significant_up_count = sum(deseq_sig & deseq_raw$log2FoldChange > 0, na.rm = TRUE), significant_down_count = sum(deseq_sig & deseq_raw$log2FoldChange < 0, na.rm = TRUE), size_factors = as.list(setNames(as.numeric(sizeFactors(dds)), samples)), rows = clean_frame(deseq_rows, colnames(deseq_rows), 100L)),
  edger = list(method = "edgeR TMM + quasi-likelihood negative-binomial GLM", version = as.character(packageVersion("edgeR")), significant_count = sum(edge_sig, na.rm = TRUE), significant_up_count = sum(edge_sig & edge_rows$log2_fold_change > 0, na.rm = TRUE), significant_down_count = sum(edge_sig & edge_rows$log2_fold_change < 0, na.rm = TRUE), normalization_factors = as.list(setNames(as.numeric(y$samples$norm.factors), rownames(y$samples))), rows = clean_frame(edge_rows, colnames(edge_rows), 100L)),
  consensus = list(significant_count = sum(consensus$significant_both, na.rm = TRUE), rows = clean_frame(consensus, colnames(consensus), 100L)),
  enrichment = list(mapping = list(mapped_count = sum(mapping_ok), tested_count = length(mapping_ok), success_fraction = sum(mapping_ok) / length(mapping_ok), id_type = id_type, species = species), kegg_source = kegg_source, go = go_rows, kegg = kegg_rows, gsea_go = gsea_go, gsea_kegg = gsea_kegg),
  warnings = as.list(unique(warnings))
)
write_json(payload, output_path, auto_unbox = TRUE, pretty = FALSE, na = "null", digits = 10)
