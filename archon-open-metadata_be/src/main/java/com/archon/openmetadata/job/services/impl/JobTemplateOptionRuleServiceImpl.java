package com.archon.openmetadata.job.services.impl;

import com.archon.openmetadata.job.models.JobTemplateOptionRule;
import com.archon.openmetadata.job.repositories.JobTemplateOptionRuleRepository;
import com.archon.openmetadata.job.services.JobTemplateOptionRuleService;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class JobTemplateOptionRuleServiceImpl implements JobTemplateOptionRuleService {
  private final JobTemplateOptionRuleRepository repository;

  @Override
  public JobTemplateOptionRule save(JobTemplateOptionRule entity) {
    return repository.save(entity);
  }

  @Override
  public JobTemplateOptionRule findById(UUID id) {
    return repository.findById(id).orElse(null);
  }

  @Override
  public List<JobTemplateOptionRule> findAll() {
    return repository.findAll();
  }

  @Override
  public Page<JobTemplateOptionRule> findAll(
      Specification<JobTemplateOptionRule> spec, Pageable pageable) {
    return repository.findAll(spec, pageable);
  }

  @Override
  public void deleteById(UUID id) {
    repository.deleteById(id);
  }
}
