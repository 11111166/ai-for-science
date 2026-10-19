#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(clusterProfiler)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: build_kegg_cache.R OUTPUT_DIRECTORY")
output_directory <- args[[1]]
dir.create(output_directory, recursive = TRUE, showWarnings = FALSE)

files <- list()
for (species in c("hsa", "mmu")) {
  downloaded <- clusterProfiler:::download_KEGG(species, "KEGG", "ncbi-geneid")
  term_gene <- unique(downloaded$KEGGPATHID2EXTID[, c("from", "to")])
  term_name <- unique(downloaded$KEGGPATHID2NAME[, c("from", "to")])
  colnames(term_gene) <- c("term", "gene")
  colnames(term_name) <- c("term", "name")
  term_gene_file <- file.path(output_directory, paste0(species, "_term_gene.tsv"))
  term_name_file <- file.path(output_directory, paste0(species, "_term_name.tsv"))
  write.table(term_gene, term_gene_file, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
  write.table(term_name, term_name_file, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
  files[[species]] <- list(
    organism = if (species == "hsa") "Homo sapiens" else "Mus musculus",
    term_gene = basename(term_gene_file),
    term_name = basename(term_name_file),
    term_gene_rows = nrow(term_gene),
    term_name_rows = nrow(term_name)
  )
}

metadata <- list(
  schema_version = "1.0",
  retrieved_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
  source = "KEGG REST API",
  source_urls = c("https://rest.kegg.jp/link/{organism}/pathway", "https://rest.kegg.jp/list/pathway/{organism}", "https://rest.kegg.jp/conv/ncbi-geneid/{organism}"),
  generated_with = paste0("clusterProfiler ", packageVersion("clusterProfiler")),
  species = files
)
write_json(metadata, file.path(output_directory, "metadata.json"), pretty = TRUE, auto_unbox = TRUE)
