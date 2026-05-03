package com.archon.openmetadata.job.repositories;

import com.archon.openmetadata.job.models.JobTemplateProfile;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.stereotype.Repository;

@Repository
public interface JobTemplateProfileRepository
    extends JpaRepository<JobTemplateProfile, UUID>, JpaSpecificationExecutor<JobTemplateProfile> {}
