package com.archon.openmetadata.job.services.impl;

import com.archon.openmetadata.job.models.JobTemplateProfile;
import com.archon.openmetadata.job.repositories.JobTemplateProfileRepository;
import com.archon.openmetadata.job.services.JobTemplateProfileService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class JobTemplateProfileServiceImpl implements JobTemplateProfileService {
  private final JobTemplateProfileRepository repository;

  @Override
  public JobTemplateProfile save(JobTemplateProfile entity) {
    return repository.save(entity);
  }

  @Override
  public JobTemplateProfile findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<JobTemplateProfile> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<JobTemplateProfile> findAll(
      Specification<JobTemplateProfile> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
