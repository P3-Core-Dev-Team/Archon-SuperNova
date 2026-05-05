package com.archon.openmetadata.metadata.dto;

import lombok.Data;
import java.util.UUID;

@Data
public class DomainGroupFilterBean {
  private String searchText;
  private UUID jobId;
}
