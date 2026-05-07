package com.archon.openmetadata.job.services.impl;

import com.archon.openmetadata.job.dto.ConnectionProfileDto;
import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.repositories.ConnectionProfileRepository;
import com.archon.openmetadata.job.services.ConnectionProfileService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class ConnectionProfileServiceImpl implements ConnectionProfileService {
  private final ConnectionProfileRepository repository;

  @Override
  public ConnectionProfile save(ConnectionProfile entity) {
    if (entity.getPass() != null) {
        try {
            // Obfuscate by storing what was sent, but we'll decode for testing/execution
            // Actually, usually we store it as provided if FE encodes it.
        } catch (Exception e) { }
    }
    if (entity.getDbType() != null && entity.getHost() != null && entity.getPort() != null && entity.getDatabaseName() != null) {
        String url = entity.getDbType().generateUrl(entity.getHost(), entity.getPort(), entity.getDatabaseName());
        entity.setUrl(url);
    }
    return repository.save(entity);
  }

  @Override
  public ConnectionProfile findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<ConnectionProfile> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<ConnectionProfile> findAll(Specification<ConnectionProfile> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }

  @Override
  public boolean testConnection(ConnectionProfileDto entity) {
    String url = entity.getUrl();
    if (url == null || url.isBlank()) {
        if (entity.getDbType() != null && entity.getHost() != null && entity.getPort() != null && entity.getDatabaseName() != null) {
            url = entity.getDbType().generateUrl(entity.getHost(), entity.getPort(), entity.getDatabaseName());
        } else {
            return false;
        }
    }
    
    String decodedPass = entity.getPass();
    try {
        decodedPass = new String(java.util.Base64.getDecoder().decode(entity.getPass()));
    } catch (Exception e) {
        // Fallback to original if not base64
    }
    try (java.sql.Connection conn = java.sql.DriverManager.getConnection(
            url, entity.getUser(), decodedPass)) {
        return conn.isValid(5);
    } catch (Exception e) {
        return false;
    }
  }
}
