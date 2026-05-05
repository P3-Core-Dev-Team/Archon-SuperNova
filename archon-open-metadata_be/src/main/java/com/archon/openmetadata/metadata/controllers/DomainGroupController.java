package com.archon.openmetadata.metadata.controllers;

import com.archon.openmetadata.metadata.dto.DomainGroupDto;
import com.archon.openmetadata.metadata.dto.DomainGroupFilterBean;
import com.archon.openmetadata.metadata.models.DomainGroupEntity;
import com.archon.openmetadata.metadata.services.DomainGroupService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/datagroups")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class DomainGroupController {

  private final DomainGroupService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<DomainGroupEntity> pagedResourcesAssembler;

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<DomainGroupDto>>> searchAll(
      @RequestBody DomainGroupFilterBean filterBean, Pageable pageable) {
    Page<DomainGroupEntity> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();
              if (filterBean.getJobId() != null) {
                predicates.add(criteriaBuilder.equal(root.get("job").get("id"), filterBean.getJobId()));
              }
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                predicates.add(criteriaBuilder.like(root.get("groupName"), "%" + filterBean.getSearchText() + "%"));
              }
              return predicates.isEmpty() ? criteriaBuilder.conjunction() : criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(pagedResourcesAssembler.toModel(page, entity -> EntityModel.of(modelMapper.map(entity, DomainGroupDto.class))));
  }
}
