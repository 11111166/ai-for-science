#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(jsonlite)
  library(edgeR)
  library(limma)
  library(igraph)
  library(uwot)
  library(clusterProfiler)
  library(AnnotationDbi)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: omics_multimodal.R INPUT_TABLE METADATA_JSON OUTPUT_JSON")
input_path <- args[[1]]
metadata <- fromJSON(args[[2]], simplifyVector = TRUE)
output_path <- args[[3]]

required <- c("jsonlite", "edgeR", "limma", "igraph", "uwot", "clusterProfiler", "AnnotationDbi")
missing_packages <- required[!vapply(required, requireNamespace, quietly = TRUE, FUN.VALUE = logical(1))]
if (length(missing_packages)) stop(paste("missing R packages:", paste(missing_packages, collapse = ", ")))

delimiter <- if (grepl("\\.(tsv|txt)$", input_path, ignore.case = TRUE)) "\t" else ","
input <- read.table(input_path, header = TRUE, sep = delimiter, check.names = FALSE, quote = "\"", comment.char = "", stringsAsFactors = FALSE)
warnings <- character()

script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_directory <- if (length(script_argument)) dirname(normalizePath(sub("^--file=", "", script_argument[[1]]))) else getwd()
gene_kegg_cache <- normalizePath(file.path(script_directory, "..", "data", "kegg_cache_v1"), mustWork = FALSE)
metabolite_cache <- normalizePath(file.path(script_directory, "..", "data", "metabolite_kegg_cache_v1"), mustWork = FALSE)

clean_frame <- function(frame, columns = colnames(frame), limit = 100L) {
  if (is.null(frame) || !nrow(frame)) return(list())
  frame <- frame[, intersect(columns, colnames(frame)), drop = FALSE]
  frame <- head(frame, limit)
  # Emit a JSON array of records, not an array of one-row data.frames.
  lapply(seq_len(nrow(frame)), function(index) as.list(frame[index, , drop = FALSE]))
}

safe_rows <- function(expr, columns, method_name, limit = 20L) {
  tryCatch({
    value <- suppressMessages(suppressWarnings(expr))
    if (is.null(value)) return(list())
    clean_frame(as.data.frame(value), columns, limit)
  }, error = function(error) {
    warnings <<- c(warnings, paste0(method_name, "：", conditionMessage(error)))
    list()
  })
}

normalise_name <- function(value) gsub("[^a-z0-9]+", "", tolower(value))

gene_mapping <- function(features, species, requested_type) {
  org_package <- if (species == "mouse") "org.Mm.eg.db" else "org.Hs.eg.db"
  if (!requireNamespace(org_package, quietly = TRUE)) stop(paste("missing annotation package", org_package))
  orgdb <- getExportedValue(org_package, org_package)
  id_type <- requested_type
  if (id_type == "auto") {
    if (mean(grepl(if (species == "mouse") "ENSMUSG[0-9]+" else "ENSG[0-9]+", features)) >= 0.5) id_type <- "ensembl"
    else if (mean(grepl("^[0-9]+(?:\\||$)", features)) >= 0.8) id_type <- "entrez"
    else id_type <- "symbol"
  }
  extract_ids <- function(values, kind) vapply(values, function(value) {
    tokens <- trimws(strsplit(as.character(value), "|", fixed = TRUE)[[1]])
    if (kind == "ensembl") {
      pattern <- if (species == "mouse") "^ENSMUSG[0-9]+" else "^ENSG[0-9]+"
      hit <- tokens[grepl(pattern, tokens)]
      if (length(hit)) return(sub("\\..*$", "", hit[[1]]))
    }
    if (kind == "entrez") {
      hit <- tokens[grepl("^[0-9]+$", tokens)]
      if (length(hit)) return(hit[[1]])
    }
    if (kind == "uniprot") {
      hit <- tokens[grepl("^[A-NR-Z0-9][A-Z0-9]{5,9}(?:-[0-9]+)?$", tokens)]
      if (length(hit)) return(sub("-[0-9]+$", "", hit[[1]]))
    }
    tokens[[1]]
  }, character(1))
  keytype <- c(symbol = "SYMBOL", ensembl = "ENSEMBL", entrez = "ENTREZID", uniprot = "UNIPROT")[[id_type]]
  source_ids <- extract_ids(features, id_type)
  safe_map_ids <- function(values, type) tryCatch(
    suppressMessages(mapIds(orgdb, keys = unique(values), keytype = type, column = "ENTREZID", multiVals = "first")),
    error = function(error) setNames(rep(NA_character_, length(unique(values))), unique(values))
  )
  mapped <- safe_map_ids(source_ids, keytype)
  entrez <- unname(mapped[source_ids])
  if (requested_type == "auto" && id_type == "symbol" && any(is.na(entrez))) {
    uniprot_ids <- extract_ids(features, "uniprot")
    fallback <- safe_map_ids(uniprot_ids, "UNIPROT")
    missing <- is.na(entrez) | !nzchar(entrez)
    entrez[missing] <- unname(fallback[uniprot_ids[missing]])
  }
  list(entrez = entrez, id_type = id_type, orgdb = orgdb, species = species)
}

gene_enrichment <- function(features, statistic, significant, direction, species, id_type, min_set = 5L) {
  mapping <- gene_mapping(features, species, id_type)
  entrez <- mapping$entrez
  mapped <- !is.na(entrez) & nzchar(entrez)
  universe <- unique(entrez[mapped])
  up <- unique(entrez[mapped & significant & direction > 0])
  down <- unique(entrez[mapped & significant & direction < 0])
  ora_columns <- c("ID", "Description", "ONTOLOGY", "GeneRatio", "BgRatio", "pvalue", "p.adjust", "qvalue", "geneID", "Count")
  go <- list()
  for (side in c("up", "down")) {
    genes <- if (side == "up") up else down
    rows <- if (length(genes) >= 3) safe_rows(enrichGO(gene = genes, universe = universe, OrgDb = mapping$orgdb, keyType = "ENTREZID", ont = "ALL", pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1, readable = TRUE, minGSSize = min_set), ora_columns, paste("GO", side)) else list()
    if (length(rows)) for (index in seq_along(rows)) rows[[index]]$direction <- side
    go <- c(go, rows)
  }
  code <- if (species == "mouse") "mmu" else "hsa"
  gene_file <- file.path(gene_kegg_cache, paste0(code, "_term_gene.tsv"))
  name_file <- file.path(gene_kegg_cache, paste0(code, "_term_name.tsv"))
  cached <- file.exists(gene_file) && file.exists(name_file)
  if (cached) {
    term_gene <- read.delim(gene_file, colClasses = "character", check.names = FALSE)
    term_name <- read.delim(name_file, colClasses = "character", check.names = FALSE)
  }
  kegg <- list()
  for (side in c("up", "down")) {
    genes <- if (side == "up") up else down
    rows <- if (length(genes) >= 3) {
      if (cached) safe_rows(enricher(gene = genes, universe = universe, TERM2GENE = term_gene, TERM2NAME = term_name, pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1, minGSSize = min_set), ora_columns, paste("KEGG", side)) else list()
    } else list()
    if (length(rows)) for (index in seq_along(rows)) rows[[index]]$direction <- side
    kegg <- c(kegg, rows)
  }
  ranks <- data.frame(entrez = entrez, statistic = statistic)
  ranks <- ranks[!is.na(ranks$entrez) & is.finite(ranks$statistic), , drop = FALSE]
  ranks <- ranks[order(-abs(ranks$statistic)), , drop = FALSE]
  ranks <- ranks[!duplicated(ranks$entrez), , drop = FALSE]
  ranked <- sort(setNames(ranks$statistic, ranks$entrez), decreasing = TRUE)
  gsea_columns <- c("ID", "Description", "setSize", "enrichmentScore", "NES", "pvalue", "p.adjust", "qvalue", "rank", "leading_edge", "core_enrichment")
  gsea_go <- if (length(ranked) >= 20) safe_rows(gseGO(geneList = ranked, OrgDb = mapping$orgdb, keyType = "ENTREZID", ont = "ALL", minGSSize = min_set, maxGSSize = 500, pvalueCutoff = 1, pAdjustMethod = "BH", by = "fgsea", verbose = FALSE), gsea_columns, "GO GSEA") else list()
  gsea_kegg <- if (length(ranked) >= 20 && cached) safe_rows(GSEA(geneList = ranked, TERM2GENE = term_gene, TERM2NAME = term_name, minGSSize = min_set, maxGSSize = 500, pvalueCutoff = 1, pAdjustMethod = "BH", by = "fgsea", verbose = FALSE), gsea_columns, "KEGG GSEA") else list()
  list(
    mapping = list(mapped_count = sum(mapped), tested_count = length(mapped), success_fraction = sum(mapped) / max(1, length(mapped)), id_type = mapping$id_type, species = species),
    kegg_source = if (cached) "versioned_KEGG_REST_cache" else "unavailable",
    go = go, kegg = kegg, gsea_go = gsea_go, gsea_kegg = gsea_kegg
  )
}

run_single_cell <- function() {
  if (nrow(input) < 20 || nrow(input) > 5000) stop("complete single-cell analysis requires 20-5,000 cells")
  columns <- unlist(metadata$metadata_columns)
  required_columns <- c("cell_id", "sample_id", "group")
  if (!all(required_columns %in% names(columns)) || !all(columns[required_columns] %in% colnames(input))) stop("single-cell metadata columns are missing")
  gene_columns <- setdiff(colnames(input), unname(columns))
  if (length(gene_columns) < 10 || length(gene_columns) > 2000) stop("complete single-cell analysis requires 10-2,000 genes")
  counts <- as.matrix(input[, gene_columns, drop = FALSE])
  storage.mode(counts) <- "double"
  if (any(!is.finite(counts)) || any(counts < 0) || any(abs(counts - round(counts)) > 1e-8)) stop("single-cell workflow requires complete non-negative integer counts")
  selected <- input[[columns[["group"]]]] %in% c(metadata$reference_group, metadata$comparison_group)
  input_selected <- input[selected, , drop = FALSE]
  counts <- counts[selected, , drop = FALSE]
  if (nrow(counts) < 20) stop("fewer than 20 cells remain in the selected groups")
  total <- rowSums(counts)
  detected <- rowSums(counts > 0)
  mito_columns <- grepl("^(MT-|mt-)", gene_columns)
  mito_fraction <- if (any(mito_columns)) rowSums(counts[, mito_columns, drop = FALSE]) / pmax(total, 1) else rep(0, nrow(counts))
  min_genes <- max(5L, floor(as.numeric(quantile(detected, 0.01, names = FALSE))))
  log_total <- log1p(total)
  mad_total <- mad(log_total, constant = 1)
  library_ok <- if (is.finite(mad_total) && mad_total > 0) abs(log_total - median(log_total)) <= 4 * mad_total else rep(TRUE, length(total))
  keep_cell <- total > 0 & detected >= min_genes & mito_fraction <= 0.20 & library_ok
  if (sum(keep_cell) < 20) stop("cell QC retained fewer than 20 cells")
  input_selected <- input_selected[keep_cell, , drop = FALSE]
  counts <- counts[keep_cell, , drop = FALSE]
  total <- total[keep_cell]
  detected <- detected[keep_cell]
  mito_fraction <- mito_fraction[keep_cell]
  keep_gene <- colSums(counts > 0) >= 3
  counts <- counts[, keep_gene, drop = FALSE]
  genes <- gene_columns[keep_gene]
  if (ncol(counts) < 10) stop("gene filtering retained fewer than 10 genes")
  normalised <- log1p(counts / pmax(rowSums(counts), 1) * 10000)
  variances <- apply(normalised, 2, var)
  hvg_count <- min(2000L, sum(is.finite(variances) & variances > 0))
  hvg <- names(sort(variances[is.finite(variances) & variances > 0], decreasing = TRUE))[seq_len(hvg_count)]
  scaled <- scale(normalised[, hvg, drop = FALSE])
  scaled[!is.finite(scaled)] <- 0
  pca <- prcomp(scaled, center = FALSE, scale. = FALSE, rank. = min(20L, nrow(scaled) - 1L, ncol(scaled)))
  pcs_used <- min(10L, ncol(pca$x))
  pc <- pca$x[, seq_len(pcs_used), drop = FALSE]
  distances <- as.matrix(dist(pc))
  neighbours <- min(15L, nrow(pc) - 1L)
  neighbour_index <- t(vapply(seq_len(nrow(pc)), function(index) order(distances[index, ])[seq_len(neighbours + 1L)][-1L], integer(neighbours)))
  edge_matrix <- do.call(rbind, lapply(seq_len(nrow(pc)), function(index) cbind(index, neighbour_index[index, ])))
  graph <- igraph::simplify(graph_from_edgelist(edge_matrix, directed = FALSE))
  E(graph)$weight <- 1 / (1 + apply(igraph::ends(graph, E(graph)), 1, function(pair) distances[as.integer(pair[[1]]), as.integer(pair[[2]])]))
  set.seed(20260920)
  communities <- cluster_leiden(graph, objective_function = "modularity", weights = E(graph)$weight, resolution = 1, n_iterations = 5)
  cluster <- as.integer(membership(communities)) - 1L
  set.seed(20260920)
  set.seed(20260920)
  embedding <- uwot::umap(
    pca$x[, seq_len(pcs_used), drop = FALSE],
    n_neighbors = min(neighbours, nrow(pca$x) - 1L), min_dist = 0.30,
    n_components = 2L, metric = "euclidean", n_threads = 1L,
    init = "spectral", n_epochs = 500L, fast_sgd = FALSE,
    ret_model = FALSE, verbose = FALSE
  )
  cell_type <- if ("cell_type" %in% names(columns)) as.character(input_selected[[columns[["cell_type"]]]]) else rep("未提供", nrow(input_selected))
  cluster_rows <- lapply(sort(unique(cluster)), function(id) {
    members <- which(cluster == id)
    types <- sort(table(cell_type[members]), decreasing = TRUE)
    list(cluster = id, cell_count = length(members), dominant_annotation = names(types)[[1]], annotation_fraction = as.numeric(types[[1]]) / length(members))
  })
  marker_frames <- list()
  for (id in sort(unique(cluster))) {
    inside <- cluster == id
    if (sum(inside) < 3 || sum(!inside) < 3) next
    marker <- lapply(seq_len(ncol(normalised)), function(index) {
      test <- suppressWarnings(wilcox.test(normalised[inside, index], normalised[!inside, index], exact = FALSE))
      data.frame(cluster = id, feature = genes[[index]], log2_fold_change = mean(normalised[inside, index]) - mean(normalised[!inside, index]), pct_in = mean(counts[inside, index] > 0), pct_out = mean(counts[!inside, index] > 0), p_value = test$p.value)
    })
    marker <- do.call(rbind, marker)
    marker$adjusted_p_value <- p.adjust(marker$p_value, method = "BH")
    marker <- marker[order(marker$adjusted_p_value, -marker$log2_fold_change), , drop = FALSE]
    marker_frames[[length(marker_frames) + 1L]] <- head(marker, 20L)
  }
  markers <- if (length(marker_frames)) do.call(rbind, marker_frames) else data.frame()
  sample_ids <- as.character(input_selected[[columns[["sample_id"]]]])
  sample_groups <- as.character(input_selected[[columns[["group"]]]])
  samples <- unique(sample_ids)
  group_by_sample <- vapply(samples, function(sample) {
    values <- unique(sample_groups[sample_ids == sample])
    if (length(values) != 1) stop(paste("sample has conflicting groups", sample))
    values[[1]]
  }, character(1))
  if (sum(group_by_sample == metadata$reference_group) < 2 || sum(group_by_sample == metadata$comparison_group) < 2) stop("pseudobulk requires at least two independent samples per group")
  pseudobulk <- vapply(samples, function(sample) colSums(counts[sample_ids == sample, , drop = FALSE]), numeric(ncol(counts)))
  rownames(pseudobulk) <- genes
  condition <- factor(group_by_sample, levels = c(metadata$reference_group, metadata$comparison_group))
  y <- DGEList(counts = pseudobulk, group = condition)
  design <- model.matrix(~ condition)
  keep <- filterByExpr(y, design = design)
  y <- y[keep, , keep.lib.sizes = FALSE]
  y <- calcNormFactors(y, method = "TMM")
  y <- estimateDisp(y, design)
  fit <- glmQLFit(y, design, robust = TRUE)
  qlf <- glmQLFTest(fit, coef = ncol(design))
  differential <- topTags(qlf, n = Inf, sort.by = "PValue")$table
  differential$feature <- rownames(differential)
  differential_rows <- data.frame(feature = differential$feature, log2_fold_change = differential$logFC, log_cpm = differential$logCPM, f_statistic = differential$F, p_value = differential$PValue, adjusted_p_value = differential$FDR)
  fdr <- as.numeric(metadata$fdr_threshold)
  effect <- as.numeric(metadata$effect_threshold)
  significant <- differential_rows$adjusted_p_value <= fdr & abs(differential_rows$log2_fold_change) >= effect
  enrichment <- gene_enrichment(differential_rows$feature, differential_rows$f_statistic * sign(differential_rows$log2_fold_change), significant, differential_rows$log2_fold_change, metadata$species, metadata$gene_id_type, min_set = 3L)
  cells <- data.frame(
    cell_id = as.character(input_selected[[columns[["cell_id"]]]]), sample_id = sample_ids, group = sample_groups,
    supplied_cell_type = cell_type, cluster = cluster, x = embedding[, 1], y = embedding[, 2], pc1 = pca$x[, 1], pc2 = if (ncol(pca$x) >= 2) pca$x[, 2] else 0,
    total_counts = total, detected_genes = detected, mito_fraction = mito_fraction, stringsAsFactors = FALSE
  )
  list(
    status = "completed",
    input = list(input_cell_count = sum(selected), retained_cell_count = nrow(counts), input_gene_count = length(gene_columns), retained_gene_count = ncol(counts), sample_count = length(samples), species = metadata$species),
    qc = list(min_detected_genes = min_genes, max_mito_fraction = 0.20, filtered_cell_count = sum(!keep_cell), min_cells_per_gene = 3),
    normalisation = list(method = "library-size normalisation to 10,000 + log1p", hvg_method = "log-normalised variance ranking", hvg_count = hvg_count),
    embedding = list(method = "PCA + kNN + Leiden + UMAP", edge_count = ecount(graph), pcs_used = pcs_used, neighbours = neighbours, cluster_count = length(unique(cluster)), clusters = cluster_rows, cells = clean_frame(cells, colnames(cells), 5000L)),
    markers = list(method = "Wilcoxon rank-sum, cluster versus all other cells, BH-FDR", rows = clean_frame(markers, colnames(markers), 200L)),
    pseudobulk = list(method = "edgeR TMM + quasi-likelihood GLM on sample_id pseudobulk", version = as.character(packageVersion("edgeR")), significant_count = sum(significant, na.rm = TRUE), significant_up_count = sum(significant & differential_rows$log2_fold_change > 0, na.rm = TRUE), significant_down_count = sum(significant & differential_rows$log2_fold_change < 0, na.rm = TRUE), rows = clean_frame(differential_rows, colnames(differential_rows), 100L)),
    enrichment = enrichment,
    warnings = as.list(unique(c(warnings, "UMAP 用于可视化而非统计检验；聚类来自 PCA-kNN 图上的 Leiden。", "自动注释仅汇总上传文件已有 cell_type；未提供参考标签时须人工核对标记基因。")))
  )
}

run_proteomics <- function() {
  samples <- c(metadata$reference_samples, metadata$comparison_samples)
  if (!all(samples %in% colnames(input))) stop("selected proteomics samples are missing")
  if (nrow(input) < 10 || nrow(input) > 10000 || length(samples) < 4 || length(samples) > 50) stop("proteomics analysis requires 10-10,000 proteins and 4-50 samples")
  features <- make.unique(trimws(as.character(input[[1]])))
  abundance <- as.matrix(input[, samples, drop = FALSE])
  storage.mode(abundance) <- "double"
  if (metadata$scale == "raw" && any(abundance < 0, na.rm = TRUE)) stop("raw protein abundance cannot contain negative values")
  if (isTRUE(metadata$zero_is_missing)) abundance[abundance == 0] <- NA
  reference_count <- length(metadata$reference_samples)
  valid_reference <- rowSums(is.finite(abundance[, seq_len(reference_count), drop = FALSE]))
  valid_comparison <- rowSums(is.finite(abundance[, reference_count + seq_along(metadata$comparison_samples), drop = FALSE]))
  minimum <- 2L
  keep <- valid_reference >= minimum & valid_comparison >= minimum
  if (sum(keep) < 10) stop("fewer than 10 proteins have at least two values in both groups")
  analysed <- abundance[keep, , drop = FALSE]
  analysed_features <- features[keep]
  if (metadata$scale == "raw") {
    if (any(analysed <= 0, na.rm = TRUE)) stop("positive protein intensities are required before log2 transformation")
    transformed <- log2(analysed)
  } else transformed <- analysed
  medians <- apply(transformed, 2, median, na.rm = TRUE)
  target <- median(medians)
  offsets <- medians - target
  normalised <- sweep(transformed, 2, offsets, "-")
  condition <- factor(c(rep("reference", reference_count), rep("comparison", length(metadata$comparison_samples))), levels = c("reference", "comparison"))
  design <- model.matrix(~ condition)
  rownames(normalised) <- analysed_features
  fit <- eBayes(lmFit(normalised, design), trend = TRUE)
  table <- topTable(fit, coef = ncol(design), number = Inf, sort.by = "P")
  table$feature <- rownames(table)
  rows <- data.frame(feature = table$feature, log2_fold_change = table$logFC, average_log2_abundance = table$AveExpr, statistic = table$t, p_value = table$P.Value, adjusted_p_value = table$adj.P.Val, b_statistic = table$B)
  fdr <- as.numeric(metadata$fdr_threshold)
  effect <- as.numeric(metadata$effect_threshold)
  significant <- rows$adjusted_p_value <= fdr & abs(rows$log2_fold_change) >= effect
  missing_candidates <- data.frame(feature = features, reference_detected = valid_reference, comparison_detected = valid_comparison, direction = ifelse(valid_reference == 0 & valid_comparison >= minimum, "comparison_only", ifelse(valid_comparison == 0 & valid_reference >= minimum, "reference_only", "other")), stringsAsFactors = FALSE)
  missing_candidates <- missing_candidates[missing_candidates$direction != "other", , drop = FALSE]
  enrichment <- gene_enrichment(rows$feature, rows$statistic, significant, rows$log2_fold_change, metadata$species, metadata$gene_id_type, min_set = 5L)
  list(
    status = "completed",
    input = list(input_protein_count = nrow(input), modelled_protein_count = nrow(rows), sample_count = length(samples), scale = metadata$scale, zero_is_missing = metadata$zero_is_missing, species = metadata$species),
    filtering = list(min_values_per_group = minimum, excluded_from_limma = nrow(input) - sum(keep), group_specific_missing_count = nrow(missing_candidates)),
    normalisation = list(method = if (metadata$scale == "raw") "log2 + sample median centering" else "sample median centering on supplied log2 values", offsets = as.list(setNames(as.numeric(offsets), samples))),
    limma = list(method = "limma empirical-Bayes linear model with trend", version = as.character(packageVersion("limma")), significant_count = sum(significant, na.rm = TRUE), significant_up_count = sum(significant & rows$log2_fold_change > 0, na.rm = TRUE), significant_down_count = sum(significant & rows$log2_fold_change < 0, na.rm = TRUE), rows = clean_frame(rows, colnames(rows), 100L)),
    missingness = list(method = "reported separately; no stochastic imputation", rows = clean_frame(missing_candidates, colnames(missing_candidates), 100L)),
    enrichment = enrichment,
    warnings = as.list(unique(c(warnings, "组特异完全缺失蛋白单列报告，不通过随机插补制造显著性。", "蛋白丰度矩阵不能回溯执行谱图搜索、PSM FDR 或肽段到蛋白汇总。")))
  )
}

fit_pls <- function(x, y, ncomp = 2L) {
  x_mean <- colMeans(x)
  y_mean <- mean(y)
  residual_x <- sweep(x, 2, x_mean, "-")
  residual_y <- y - y_mean
  components <- min(ncomp, nrow(x) - 1L, ncol(x))
  w <- matrix(0, ncol(x), components)
  p <- matrix(0, ncol(x), components)
  q <- numeric(components)
  scores <- matrix(0, nrow(x), components)
  for (component in seq_len(components)) {
    weight <- as.numeric(crossprod(residual_x, residual_y))
    norm <- sqrt(sum(weight^2))
    if (!is.finite(norm) || norm <= 1e-12) break
    weight <- weight / norm
    score <- as.numeric(residual_x %*% weight)
    denominator <- sum(score^2)
    if (denominator <= 1e-12) break
    loading <- as.numeric(crossprod(residual_x, score) / denominator)
    response <- sum(residual_y * score) / denominator
    w[, component] <- weight
    p[, component] <- loading
    q[[component]] <- response
    scores[, component] <- score
    residual_x <- residual_x - score %o% loading
    residual_y <- residual_y - score * response
  }
  active <- which(colSums(abs(w)) > 0)
  w <- w[, active, drop = FALSE]; p <- p[, active, drop = FALSE]; q <- q[active]; scores <- scores[, active, drop = FALSE]
  coefficient <- if (length(active)) w %*% solve(crossprod(p, w), q) else matrix(0, ncol(x), 1)
  list(x_mean = x_mean, y_mean = y_mean, coefficient = as.numeric(coefficient), w = w, p = p, q = q, scores = scores)
}

predict_pls <- function(model, x) as.numeric(sweep(x, 2, model$x_mean, "-") %*% model$coefficient + model$y_mean)

cross_validate_pls <- function(x, y, ncomp = 2L, folds = 5L) {
  fold_id <- integer(length(y))
  for (value in sort(unique(y))) {
    positions <- which(y == value)
    fold_id[positions] <- rep(seq_len(min(folds, length(positions))), length.out = length(positions))
  }
  prediction <- numeric(length(y))
  for (fold in sort(unique(fold_id))) {
    test <- fold_id == fold
    model <- fit_pls(x[!test, , drop = FALSE], y[!test], ncomp)
    prediction[test] <- predict_pls(model, x[test, , drop = FALSE])
  }
  classification <- as.integer(prediction >= 0.5)
  sensitivity <- mean(classification[y == 1] == 1)
  specificity <- mean(classification[y == 0] == 0)
  list(prediction = prediction, accuracy = mean(classification == y), balanced_accuracy = mean(c(sensitivity, specificity)), q2 = 1 - sum((y - prediction)^2) / sum((y - mean(y))^2))
}

run_metabolomics <- function() {
  samples <- c(metadata$reference_samples, metadata$comparison_samples)
  if (!all(samples %in% colnames(input))) stop("selected metabolomics samples are missing")
  if (nrow(input) < 10 || nrow(input) > 5000 || length(samples) < 6 || length(samples) > 100) stop("metabolomics analysis requires 10-5,000 features and 6-100 samples")
  features <- make.unique(trimws(as.character(input[[1]])))
  values <- as.matrix(input[, samples, drop = FALSE])
  storage.mode(values) <- "double"
  if (any(values < 0, na.rm = TRUE) && metadata$scale != "log2") stop("raw metabolite intensity cannot contain negative values")
  if (isTRUE(metadata$zero_is_missing)) values[values == 0] <- NA
  reference_count <- length(metadata$reference_samples)
  reference_columns <- seq_len(reference_count)
  comparison_columns <- reference_count + seq_along(metadata$comparison_samples)
  missing_fraction <- rowMeans(!is.finite(values))
  keep <- missing_fraction <= 0.40 & rowSums(is.finite(values[, reference_columns, drop = FALSE])) >= 2 & rowSums(is.finite(values[, comparison_columns, drop = FALSE])) >= 2
  if (sum(keep) < 10) stop("fewer than 10 metabolites remain after missingness filtering")
  matrix <- values[keep, , drop = FALSE]
  analysed_features <- features[keep]
  imputed <- 0L
  for (row in seq_len(nrow(matrix))) {
    missing <- !is.finite(matrix[row, ])
    if (any(missing)) {
      observed <- matrix[row, !missing]
      replacement <- if (metadata$scale == "log2") median(observed) else min(observed[observed > 0]) / 2
      matrix[row, missing] <- replacement
      imputed <- imputed + sum(missing)
    }
  }
  if (metadata$scale == "log2") {
    pqn_factors <- rep(1, ncol(matrix))
    log_matrix <- matrix
    normalisation_method <- "supplied log2 values; feature-median fill for limited missingness"
  } else {
    reference_profile <- apply(matrix, 1, median)
    pqn_factors <- apply(matrix, 2, function(column) median(column[reference_profile > 0] / reference_profile[reference_profile > 0]))
    pqn_factors[!is.finite(pqn_factors) | pqn_factors <= 0] <- 1
    pqn <- sweep(matrix, 2, pqn_factors, "/")
    log_matrix <- log2(pqn)
    normalisation_method <- "half-minimum fill + probabilistic quotient normalisation + log2"
  }
  rownames(log_matrix) <- analysed_features
  feature_sd <- apply(log_matrix, 1, sd)
  pareto <- sweep(log_matrix, 1, rowMeans(log_matrix), "-")
  pareto <- sweep(pareto, 1, sqrt(pmax(feature_sd, 1e-12)), "/")
  x <- t(pareto)
  y <- c(rep(0, reference_count), rep(1, length(metadata$comparison_samples)))
  pca <- prcomp(x, center = FALSE, scale. = FALSE, rank. = min(10L, nrow(x) - 1L, ncol(x)))
  cv <- cross_validate_pls(x, y, ncomp = 2L, folds = min(5L, reference_count, length(metadata$comparison_samples)))
  set.seed(20260920)
  permutations <- 100L
  permuted <- replicate(permutations, cross_validate_pls(x, sample(y), ncomp = 2L, folds = min(5L, reference_count, length(metadata$comparison_samples)))$balanced_accuracy)
  permutation_p <- (1 + sum(permuted >= cv$balanced_accuracy)) / (permutations + 1)
  pls <- fit_pls(x, y, 2L)
  component_ss <- if (ncol(pls$scores)) vapply(seq_len(ncol(pls$scores)), function(index) sum(pls$scores[, index]^2) * pls$q[[index]]^2, numeric(1)) else numeric()
  vip <- if (length(component_ss) && sum(component_ss) > 0) sqrt(ncol(x) * rowSums(sweep(pls$w^2, 2, component_ss, "*")) / sum(component_ss)) else rep(0, ncol(x))
  design <- model.matrix(~ factor(y, levels = c(0, 1)))
  fit <- eBayes(lmFit(log_matrix, design), trend = TRUE)
  table <- topTable(fit, coef = ncol(design), number = Inf, sort.by = "P")
  table$feature <- rownames(table)
  rows <- data.frame(feature = table$feature, log2_fold_change = table$logFC, average_log2_abundance = table$AveExpr, statistic = table$t, p_value = table$P.Value, adjusted_p_value = table$adj.P.Val, b_statistic = table$B)
  vip_rows <- data.frame(feature = colnames(x), vip = vip, coefficient = pls$coefficient, stringsAsFactors = FALSE)
  vip_rows <- vip_rows[order(-vip_rows$vip), , drop = FALSE]
  fdr <- as.numeric(metadata$fdr_threshold)
  effect <- as.numeric(metadata$effect_threshold)
  significant <- rows$adjusted_p_value <= fdr & abs(rows$log2_fold_change) >= effect
  alias_file <- file.path(metabolite_cache, "compound_alias.tsv")
  gene_file <- file.path(metabolite_cache, "compound_term_gene.tsv")
  name_file <- file.path(metabolite_cache, "compound_term_name.tsv")
  enrichment <- list(mapping = list(mapped_count = 0, tested_count = nrow(rows), success_fraction = 0, id_type = metadata$metabolite_id_type), kegg_source = "unavailable", ora = list(), gsea = list())
  if (file.exists(alias_file) && file.exists(gene_file) && file.exists(name_file)) {
    aliases <- read.delim(alias_file, colClasses = "character", check.names = FALSE)
    aliases <- aliases[!duplicated(aliases$normalised_name), , drop = FALSE]
    alias_lookup <- setNames(aliases$compound_id, aliases$normalised_name)
    compounds <- ifelse(grepl("^(?:cpd:)?C[0-9]{5}$", rows$feature), sub("^cpd:", "", rows$feature), unname(alias_lookup[normalise_name(rows$feature)]))
    mapped <- !is.na(compounds) & nzchar(compounds)
    term_gene <- read.delim(gene_file, colClasses = "character", check.names = FALSE)
    term_name <- read.delim(name_file, colClasses = "character", check.names = FALSE)
    universe <- unique(compounds[mapped])
    selected <- unique(compounds[mapped & significant])
    ora_columns <- c("ID", "Description", "GeneRatio", "BgRatio", "pvalue", "p.adjust", "qvalue", "geneID", "Count")
    ora <- if (length(selected) >= 2) safe_rows(enricher(gene = selected, universe = universe, TERM2GENE = term_gene, TERM2NAME = term_name, pAdjustMethod = "BH", pvalueCutoff = 1, qvalueCutoff = 1, minGSSize = 2), ora_columns, "metabolite KEGG ORA") else list()
    rank_frame <- data.frame(compound = compounds, statistic = rows$statistic)
    rank_frame <- rank_frame[mapped & is.finite(rank_frame$statistic), , drop = FALSE]
    rank_frame <- rank_frame[order(-abs(rank_frame$statistic)), , drop = FALSE]
    rank_frame <- rank_frame[!duplicated(rank_frame$compound), , drop = FALSE]
    ranks <- sort(setNames(rank_frame$statistic, rank_frame$compound), decreasing = TRUE)
    gsea_columns <- c("ID", "Description", "setSize", "enrichmentScore", "NES", "pvalue", "p.adjust", "qvalue", "rank", "leading_edge", "core_enrichment")
    gsea <- if (length(ranks) >= 15) safe_rows(GSEA(geneList = ranks, TERM2GENE = term_gene, TERM2NAME = term_name, minGSSize = 2, maxGSSize = 200, pvalueCutoff = 1, pAdjustMethod = "BH", by = "fgsea", verbose = FALSE), gsea_columns, "metabolite KEGG GSEA") else list()
    enrichment <- list(mapping = list(mapped_count = sum(mapped), tested_count = length(mapped), success_fraction = sum(mapped) / length(mapped), id_type = metadata$metabolite_id_type), kegg_source = "versioned_KEGG_REST_compound_cache", ora = ora, gsea = gsea)
  }
  pca_points <- data.frame(sample = samples, group = ifelse(y == 0, "reference", "comparison"), pc1 = pca$x[, 1], pc2 = if (ncol(pca$x) >= 2) pca$x[, 2] else 0)
  pls_points <- data.frame(sample = samples, group = ifelse(y == 0, "reference", "comparison"), component1 = if (ncol(pls$scores) >= 1) pls$scores[, 1] else 0, component2 = if (ncol(pls$scores) >= 2) pls$scores[, 2] else 0, cv_prediction = cv$prediction)
  list(
    status = "completed",
    input = list(input_feature_count = nrow(input), retained_feature_count = nrow(rows), sample_count = length(samples), scale = metadata$scale, zero_is_missing = metadata$zero_is_missing),
    preprocessing = list(max_missing_fraction = 0.40, excluded_feature_count = nrow(input) - sum(keep), imputed_value_count = imputed, imputation = if (metadata$scale == "log2") "feature median" else "half observed minimum", normalisation = normalisation_method, scaling = "Pareto" , pqn_factors = as.list(setNames(as.numeric(pqn_factors), samples))),
    pca = list(explained_variance = (pca$sdev^2 / sum(pca$sdev^2))[seq_len(min(5L, length(pca$sdev)))], points = clean_frame(pca_points, colnames(pca_points), 100L)),
    plsda = list(method = "two-component PLS-DA with stratified cross-validation", accuracy = cv$accuracy, balanced_accuracy = cv$balanced_accuracy, q2 = cv$q2, permutation_count = permutations, permutation_p_value = permutation_p, points = clean_frame(pls_points, colnames(pls_points), 100L), vip = clean_frame(vip_rows, colnames(vip_rows), 100L)),
    limma = list(method = "limma empirical-Bayes linear model on PQN/log2 values", version = as.character(packageVersion("limma")), significant_count = sum(significant, na.rm = TRUE), significant_up_count = sum(significant & rows$log2_fold_change > 0, na.rm = TRUE), significant_down_count = sum(significant & rows$log2_fold_change < 0, na.rm = TRUE), rows = clean_frame(rows, colnames(rows), 100L)),
    enrichment = enrichment,
    warnings = as.list(unique(c(warnings, "PLS-DA 必须与交叉验证、Q² 和 100 次标签置换结果一起解释；置换 P 值不达标时不得据此声称分组可预测。", "没有 blank/QC/批次元数据时无法执行空白扣除、QC-RLSC 漂移校正或批次校正。")))
  )
}

modality <- as.character(metadata$modality)
payload <- switch(modality,
  single_cell = run_single_cell(),
  proteomics = run_proteomics(),
  metabolomics = run_metabolomics(),
  stop("unsupported multimodal analysis modality")
)
payload$runtime <- list(rscript = R.home(), script = "omics_multimodal.R", execution = "local_isolated_subprocess", threads = 1)
write_json(payload, output_path, auto_unbox = TRUE, pretty = FALSE, na = "null", digits = 10)
