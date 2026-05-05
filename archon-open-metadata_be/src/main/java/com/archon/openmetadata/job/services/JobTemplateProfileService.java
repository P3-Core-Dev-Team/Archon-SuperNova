package com.archon.openmetadata.job.services;

import com.archon.openmetadata.job.models.JobTemplateProfile;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;

public interface JobTemplateProfileService {
  JobTemplateProfile save(JobTemplateProfile entity);

  JobTemplateProfile findById(UUID id);

  List<JobTemplateProfile> findAll();

  Page<JobTemplateProfile> findAll(Specification<JobTemplateProfile> spec, Pageable pageable);

  void deleteById(UUID id);
}
