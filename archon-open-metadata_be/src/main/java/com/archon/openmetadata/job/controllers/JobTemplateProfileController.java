package com.archon.openmetadata.job.controllers;

import com.archon.openmetadata.job.dto.JobTemplateProfileDto;
import com.archon.openmetadata.job.dto.JobTemplateProfileFilterBean;
import com.archon.openmetadata.job.models.JobTemplateProfile;
import com.archon.openmetadata.job.services.JobTemplateProfileService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/jobtemplateprofiles")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class JobTemplateProfileController {

  private final JobTemplateProfileService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<JobTemplateProfile> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<JobTemplateProfileDto>>> fetchAll(
      Pageable pageable) {
    Page<JobTemplateProfile> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, JobTemplateProfileDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(JobTemplateProfileController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<JobTemplateProfileDto>> getById(@PathVariable UUID id) {
    JobTemplateProfile entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, JobTemplateProfileDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobTemplateProfileController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<JobTemplateProfileDto>> create(
      @RequestBody JobTemplateProfileDto dto) {
    JobTemplateProfile saved = service.save(modelMapper.map(dto, JobTemplateProfile.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, JobTemplateProfileDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobTemplateProfileController.class)
                        .getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<JobTemplateProfileDto>> update(
      @PathVariable UUID id, @RequestBody JobTemplateProfileDto dto) {
    dto.setId(id);
    JobTemplateProfile updated = service.save(modelMapper.map(dto, JobTemplateProfile.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, JobTemplateProfileDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(JobTemplateProfileController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<JobTemplateProfileDto>>> searchAll(
      @RequestBody JobTemplateProfileFilterBean filterBean, Pageable pageable) {
    Page<JobTemplateProfile> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, JobTemplateProfileDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(JobTemplateProfileController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
