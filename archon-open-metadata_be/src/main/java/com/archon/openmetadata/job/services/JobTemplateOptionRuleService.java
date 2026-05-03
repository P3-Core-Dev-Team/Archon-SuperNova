package com.archon.openmetadata.job.services;

import com.archon.openmetadata.job.models.JobTemplateOptionRule;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface JobTemplateOptionRuleService {
  JobTemplateOptionRule save(JobTemplateOptionRule entity);

  JobTemplateOptionRule findById(UUID id);

  List<JobTemplateOptionRule> findAll();

  Page<JobTemplateOptionRule> findAll(Specification<JobTemplateOptionRule> spec, Pageable pageable);

  void deleteById(UUID id);
}
