package com.archon.openmetadata.metadata.dto;

import lombok.Data;

@Data
public class ColumnEntityFilterBean {
  private String searchText;
  private java.util.UUID jobId;
  private Boolean isSensitive;
}
