package com.archon.openmetadata.job.dto;

import com.archon.openmetadata.common.dto.AuditModelDto;
import com.archon.openmetadata.iam.dto.*;
import com.archon.openmetadata.metadata.dto.*;
import java.util.UUID;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class ConnectionProfileDto extends AuditModelDto {
  private UUID id;
  private String profileName;
  private String url;
  private String user;
  private String pass;
  private String listOfSchemas;

  public String getDbType() {
    if (url != null && url.startsWith("jdbc:")) {
      String[] parts = url.split(":");
      if (parts.length > 2) return parts[1];
    }
    return null;
  }

  public String getHost() {
    if (url != null) {
      java.util.regex.Matcher m = java.util.regex.Pattern.compile("://([^:/]+)").matcher(url);
      if (m.find()) return m.group(1);
    }
    return null;
  }

  public Integer getPort() {
    if (url != null) {
      java.util.regex.Matcher m = java.util.regex.Pattern.compile(":(\\d+)/").matcher(url);
      if (m.find()) return Integer.parseInt(m.group(1));
    }
    return null;
  }

  public String getDatabaseName() {
    if (url != null) {
      java.util.regex.Matcher m = java.util.regex.Pattern.compile("/([^/?]+)(?:\\?|$)").matcher(url);
      if (m.find()) return m.group(1);
    }
    return null;
  }
}
