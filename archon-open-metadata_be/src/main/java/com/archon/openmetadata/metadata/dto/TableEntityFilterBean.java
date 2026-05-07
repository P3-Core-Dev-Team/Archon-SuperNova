package com.archon.openmetadata.metadata.dto;

import lombok.Data;

@Data
public class TableEntityFilterBean {
  private String searchText;
  private java.util.UUID jobId;
}
